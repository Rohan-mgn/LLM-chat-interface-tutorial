"""Drawing evidence lifecycle and independently specified dimension fixtures."""
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import check_rag as fixtures
from check_drawing_analysis import drawing


def dimension_pdf():
    from pypdf import PdfReader,PdfWriter
    from pypdf.generic import DecodedStreamObject,NameObject
    reader=PdfReader(io.BytesIO(drawing()));writer=PdfWriter();writer.add_page(reader.pages[0])
    page=writer.pages[0];content=page.get_contents().get_data()
    content+=b'''\n20 240 230 80 re S
BT /F1 10 Tf 30 300 Td (T-101) Tj ET
BT /F1 10 Tf 30 280 Td (HEIGHT (mm)) Tj ET
BT /F1 10 Tf 180 280 Td (2500) Tj ET
BT /F1 10 Tf 20 220 Td (P-202 WIDTH: 12 in) Tj ET
'''
    stream=DecodedStreamObject();stream.set_data(content);page[NameObject('/Contents')]=writer._add_object(stream)
    output=io.BytesIO();writer.write(output);return output.getvalue()


class DrawingSafetyTests(unittest.TestCase):
    setUp=fixtures.Checks.setUp;tearDown=fixtures.Checks.tearDown
    upload=fixtures.Checks.upload;send=fixtures.Checks.send;rows=fixtures.Checks.rows

    def test_revision_table_corroborates_without_selecting_largest_revision(self):
        from pypdf import PdfReader,PdfWriter
        from pypdf.generic import DecodedStreamObject,NameObject
        from RAG.drawing_extract import extract_page
        import tempfile
        for entry,expected in [('07','07'),('06',None)]:
            writer=PdfWriter();writer.add_page(PdfReader(io.BytesIO(drawing())).pages[0]);page=writer.pages[0]
            table=('\n20 100 280 60 re S 60 100 m 60 160 l S '
                   '20 140 m 300 140 l S 20 120 m 300 120 l S '
                   'BT /F1 8 Tf 25 150 Td (REV) Tj ET '
                   'BT /F1 8 Tf 80 150 Td (DATE) Tj ET '
                   'BT /F1 8 Tf 150 150 Td (DESCRIPTION) Tj ET '
                   f'BT /F1 8 Tf 25 130 Td ({entry}) Tj ET '
                   'BT /F1 8 Tf 25 110 Td (99) Tj ET ').encode()
            stream=DecodedStreamObject();stream.set_data(page.get_contents().get_data()+table)
            page[NameObject('/Contents')]=writer._add_object(stream)
            with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'.artifacts') as root:
                path=Path(root)/'revision-table.pdf'
                with path.open('wb') as output:writer.write(output)
                data=extract_page(path,1)
                field=next(f for f in data['fields'] if f['key']=='revision')
                self.assertEqual(field['value'],expected)
                self.assertEqual(field['revision_table_check']['status'],'corroborated' if expected else 'conflicting')
                self.assertNotEqual(field['value'],'99')

    def test_explicit_table_dimension_and_unit_conversion_through_chat(self):
        file=self.upload('equipment.pdf',dimension_pdf(),'application/pdf')
        result=self.send('What is the height of T-101 in equipment.pdf in m?',use_files=True,file_ids=[file['id']])
        self.assertEqual(result[-1]['status'],'completed',result)
        answer=self.rows()[-1]['content'];self.assertIn('2500 mm',answer);self.assertIn('2.500 m',answer)
        self.send('What is the width of P-202 in equipment.pdf?',use_files=True,file_ids=[file['id']])
        self.assertIn('12 in',self.rows()[-1]['content'])

    def test_artifact_integrity_and_delete_outbox(self):
        from contextlib import closing
        file=self.upload('drawing.pdf',drawing(),'application/pdf')
        base=f'/conversations/{self.cid}/files/{file["id"]}/drawing'
        analysis=self.client.post(base+'/analyze',json={'page':1});self.assertEqual(analysis.status_code,200,analysis.text)
        with closing(self.app.get_connection()) as db:
            row=db.execute('SELECT stored_filename FROM drawing_artifacts WHERE file_id=?',(file['id'],)).fetchone()
            saved=db.execute('SELECT data FROM drawing_runs WHERE file_id=?',(file['id'],)).fetchone()
        self.assertIn('artifact',json.loads(saved[0]));path=self.app.documents.path(row[0]);self.assertTrue(path.exists())
        path.write_bytes(b'corrupted')
        response=self.client.post(base+'/analyze',json={'page':1});self.assertEqual(response.status_code,422)
        self.client.delete(f'/conversations/{self.cid}/files/{file["id"]}')
        self.app.documents.cleanup();self.assertFalse(path.exists())

    def test_review_change_invalidates_saved_tool_evidence_and_release_revision(self):
        from RAG.drawings import Drawings
        from RAG.evidence_release import SourceInvalidated,validate_job
        file=self.upload('drawing.pdf',drawing(),'application/pdf')
        self.send('What is the revision of drawing.pdf?',use_files=True,file_ids=[file['id']])
        tool=self.rows()[-1]['tool_evidence'][0]['id'];service=Drawings(self.app.documents)
        before=service.revision(self.cid,file['id'])
        job={'conversation_id':self.cid,'drawing_versions':{file['id']:before}}
        service.mutate(self.cid,file['id'],file['sha256'],lambda db:None)
        with self.assertRaises(SourceInvalidated):validate_job(self.app.documents,job)
        self.assertEqual(self.client.get(f'/conversations/{self.cid}/tool-evidence/{tool}').status_code,409)

    def test_zero_count_review_requires_analyzed_real_page(self):
        file=self.upload('drawing.pdf',drawing(),'application/pdf')
        base=f'/conversations/{self.cid}/files/{file["id"]}/drawing'
        analysis=self.client.post(base+'/analyze',json={'page':1}).json()
        body={'source_hash':file['sha256'],'expected_revision':analysis['revision'],'reviewer':'Synthetic test reviewer',
              'scope':{'file_id':file['id'],'source_hash':file['sha256'],'pages':[99],'bbox':None,'template_id':'gate_valve','template_version':1},
              'objects':[],'reviewed_cells':[f'99:{x}:{y}' for x in range(4) for y in range(4)],'independent':True,'exhaustive':True,'unresolved':[]}
        self.assertEqual(self.client.post(base+'/reviews/publish',json=body).status_code,422)
        self.assertEqual(self.client.get(base+'/reviews').json()['reviews'],[])
