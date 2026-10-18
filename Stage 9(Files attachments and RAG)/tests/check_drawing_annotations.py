"""Production endpoint tests for human corrections and uploaded references."""
import io
import unittest
from unittest.mock import patch
from PIL import Image,ImageDraw
import check_rag as fixtures
from check_drawing_analysis import drawing


class AnnotationTests(unittest.TestCase):
    setUp=fixtures.Checks.setUp;tearDown=fixtures.Checks.tearDown
    upload=fixtures.Checks.upload;send=fixtures.Checks.send;rows=fixtures.Checks.rows

    def test_review_draft_restoration_is_scoped_versioned_and_never_attests(self):
        f=self.upload('drawing.pdf',drawing(),'application/pdf')
        base=f'/conversations/{self.cid}/files/{f["id"]}/drawing'
        analysis=self.client.post(base+'/analyze',json={'page':1}).json()
        body={'source_hash':f['sha256'],'expected_revision':analysis['revision'],'reviewer':'Fixture reviewer',
              'scope':{'file_id':f['id'],'source_hash':f['sha256'],'pages':[1],'bbox':None,'template_id':'gate_valve','template_version':1},
              'objects':[{'id':'manual-a','page':1,'bbox':[80.,168.,112.,200.],'class':'gate valve'}],
              'reviewed_cells':[f'1:{x}:{y}' for x in range(4) for y in range(4)],
              'independent':True,'exhaustive':True,'unresolved':['Unclear internal mark in lower region']}
        saved=self.client.post(base+'/reviews/draft',json=body)
        self.assertEqual(saved.status_code,200,saved.text);record=saved.json()
        restored=self.client.get(base+'/reviews/'+record['id'])
        self.assertEqual(restored.status_code,200,restored.text)
        draft=restored.json()['data']
        self.assertEqual(draft['objects'],body['objects']);self.assertEqual(draft['unresolved'],body['unresolved'])
        self.assertFalse(draft['independent']);self.assertFalse(draft['exhaustive'])
        self.assertEqual(restored.json()['revision'],record['revision'])
        body['expected_revision']=record['revision']
        self.assertEqual(self.client.post(base+'/reviews/publish',json=body).status_code,422)
        other_cid=self.client.post('/conversations').json()['id']
        other=self.upload('other.pdf',drawing(),'application/pdf',cid=other_cid)
        self.assertNotEqual(other['id'],f['id'])
        self.assertEqual(self.client.get(f'/conversations/{other_cid}/files/{other["id"]}/drawing/reviews/'+record['id']).status_code,404)
        body['unresolved']=[' ']
        self.assertEqual(self.client.post(base+'/reviews/draft',json=body).status_code,422)
        body['unresolved']=[]
        newer=self.client.post(base+'/reviews/draft',json=body)
        self.assertEqual(newer.status_code,200,newer.text)
        self.assertEqual(self.client.get(base+'/reviews/'+record['id']).status_code,409)
        self.client.delete(f'/conversations/{self.cid}/files/{f["id"]}')
        self.assertEqual(self.client.get(base+'/reviews/'+newer.json()['id']).status_code,404)

    def test_json_result_is_rejected_if_review_changes_before_release(self):
        from RAG.drawings import Drawings
        f=self.upload('drawing.pdf',drawing(),'application/pdf')
        base=f'/conversations/{self.cid}/files/{f["id"]}/drawing'
        self.assertEqual(self.client.post(base+'/analyze',json={'page':1}).status_code,200)
        original=Drawings.templates
        def changed(service,cid,fid):
            result=original(service,cid,fid)
            service.mutate(cid,fid,f['sha256'],lambda db:None)
            return result
        with patch.object(Drawings,'templates',changed):
            response=self.client.get(base+'/templates')
        self.assertEqual(response.status_code,409)
        self.assertNotIn('gate_valve',response.text)

    def test_exhaustive_review_total_uses_reviewed_boxes_and_saved_label(self):
        f=self.upload('drawing.pdf',drawing(),'application/pdf');base=f'/conversations/{self.cid}/files/{f["id"]}/drawing'
        analysis=self.client.post(base+'/analyze',json={'page':1}).json()
        body={'source_hash':f['sha256'],'expected_revision':analysis['revision'],'reviewer':'Synthetic fixture reviewer',
              'scope':{'file_id':f['id'],'source_hash':f['sha256'],'pages':[1],'bbox':None,'template_id':'gate_valve','template_version':1},
              'objects':[{'id':'truth-a','page':1,'bbox':[80.,168.,112.,200.],'class':'gate valve'},
                         {'id':'truth-b','page':1,'bbox':[180.,168.,212.,200.],'class':'gate valve'}],
              'reviewed_cells':[f'1:{x}:{y}' for x in range(4) for y in range(4)],'independent':True,'exhaustive':True,'unresolved':[]}
        published=self.client.post(base+'/reviews/publish',json=body);self.assertEqual(published.status_code,200,published.text)
        response=self.client.post(base+'/count',json={'pages':[1],'template_id':'gate_valve'});self.assertEqual(response.status_code,200,response.text)
        count=response.json();self.assertEqual(count['label'],'Exact');self.assertEqual(count['count'],2)
        self.assertEqual({d['id'] for d in count['detections']},{'truth-a','truth-b'})
        self.assertEqual(self.client.get(base+'/counts/'+count['id']).json()['label'],'Exact')
        body['expected_revision']=count['analysis_revision'];body['independent']=False;body['exhaustive']=False
        self.assertEqual(self.client.post(base+'/reviews/draft',json=body).status_code,200)
        self.assertEqual(self.client.get(base+'/counts/'+count['id']).status_code,409)
        repeated=self.client.post(base+'/count',json={'pages':[1],'template_id':'gate_valve'})
        self.assertEqual(repeated.status_code,200,repeated.text)
        self.assertEqual(repeated.json()['label'],'Estimated')

    def test_reviewed_connection_reaches_chat_and_signal_is_not_process(self):
        f=self.upload('drawing.pdf',drawing(),'application/pdf')
        base=f'/conversations/{self.cid}/files/{f["id"]}/drawing'
        analysis=self.client.post(base+'/analyze',json={'page':1}).json();source=analysis['data']['paths'][0]['id']
        def obj(oid,kind,label,**extra):
            return {'id':oid,'kind':kind,'label':label,'page':1,'bbox':[20.,20.,550.,380.],'source_ids':[source],**extra}
        objects=[obj('a','symbol','tank A'),obj('b','symbol','pump B'),
                 obj('pipe','connection','line L-101',start='a',end='b',line_type='process',direction='undirected')]
        body={'source_hash':f['sha256'],'expected_revision':analysis['revision'],'reviewer':'Synthetic fixture reviewer',
              'objects':objects,'publish':True,'attestation':False}
        self.assertEqual(self.client.post(base+'/annotations',json=body).status_code,422)
        body['attestation']=True
        published=self.client.post(base+'/annotations',json=body);self.assertEqual(published.status_code,200,published.text)
        self.send('Which pipe connects tank A to pump B in drawing.pdf?',use_files=True,file_ids=[f['id']])
        answer=self.rows()[-1]['content'];self.assertIn('line L-101',answer);self.assertIn('human review',answer)
        self.assertIn('Flow direction is not established',answer)
        body['expected_revision']=published.json()['revision'];objects[-1]['line_type']='signal'
        response=self.client.post(base+'/annotations',json=body);self.assertEqual(response.status_code,200,response.text)
        self.send('Which pipe connects tank A to pump B in drawing.pdf?',use_files=True,file_ids=[f['id']])
        self.assertIn('unverified',self.rows()[-1]['content'])
        self.assertNotIn('through line L-101',self.rows()[-1]['content'])
        self.assertEqual(self.client.post(base+'/annotations',json=body).status_code,409)

    def test_uploaded_reference_is_scoped_and_deleted_reference_invalidates_template(self):
        f=self.upload('drawing.pdf',drawing(),'application/pdf');base=f'/conversations/{self.cid}/files/{f["id"]}/drawing'
        image=Image.new('RGB',(48,48),'white');ImageDraw.Draw(image).rectangle((10,10,35,35),outline='black',width=2)
        data=io.BytesIO();image.save(data,format='PNG');reference=self.upload('reference.png',data.getvalue(),'image/png')
        analysis=self.client.post(base+'/analyze',json={'page':1}).json()
        body={'source_hash':f['sha256'],'expected_revision':analysis['revision'],'reference_file_id':reference['id'],'name':'reference shape'}
        response=self.client.post(base+'/templates/image',json=body);self.assertEqual(response.status_code,200,response.text)
        template=response.json();self.assertEqual(template['kind'],'reference')
        other=self.client.post('/conversations').json()['id']
        foreign=self.upload('foreign.png',data.getvalue(),'image/png',cid=other)
        body.update(expected_revision=template['revision'],reference_file_id=foreign['id'])
        self.assertEqual(self.client.post(base+'/templates/image',json=body).status_code,422)
        self.client.delete(f'/conversations/{self.cid}/files/{reference["id"]}')
        self.assertNotIn(template['id'],self.client.get(base+'/templates').json()['templates'])
