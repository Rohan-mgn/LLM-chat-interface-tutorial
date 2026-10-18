"""Independent synthetic geometry plus real-document production API regressions."""
import asyncio
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

STAGE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(STAGE))
from RAG.drawing_geometry import geometry,point,box
from RAG.drawing_extract import extract_page
from RAG.drawing_symbols import count_label,deduplicate,detect_tile,builtin_templates,match_vectors
from RAG.drawing_validation import evaluate,convert,measurement
from RAG.drawing_graph import build,trace
import check_rag as fixtures


def drawing(rotation=0):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject,DictionaryObject,NameObject
    writer=PdfWriter();page=writer.add_blank_page(width=600,height=400)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    content=b'''0.5 w 350 20 m 550 20 l 550 100 l 350 100 l 350 20 l S
500 20 m 500 100 l S 350 60 m 550 60 l S
BT /F1 10 Tf 360 80 Td (DRAWING NO.) Tj ET
BT /F1 10 Tf 510 80 Td (REV.) Tj ET
BT /F1 10 Tf 360 40 Td (TEST-ENG-0001) Tj ET
BT /F1 10 Tf 510 40 Td (07) Tj ET
BT /F1 10 Tf 20 370 Td (NOT TO SCALE) Tj ET
BT /F1 10 Tf 20 350 Td (Other drawing reference WRONG-9999 revision 99) Tj ET
80 200 m 96 216 l 80 232 l 80 200 l S
112 200 m 96 216 l 112 232 l 112 200 l S
180 200 m 196 216 l 180 232 l 180 200 l S
212 200 m 196 216 l 212 232 l 212 200 l S
'''
    stream=DecodedStreamObject();stream.set_data(content);page[NameObject('/Contents')]=writer._add_object(stream)
    if rotation:page.rotate(rotation)
    output=io.BytesIO();writer.write(output);return output.getvalue()


class GeometryTests(unittest.TestCase):
    def test_rendered_landmarks_match_crop_rotation_and_user_unit(self):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject,NameObject,NumberObject,RectangleObject
        from PIL import Image
        from RAG.drawing_extract import render
        # Expected centers calculated from the independently authored rectangle,
        # not from the extractor's transform or detected path coordinates.
        centers={0:(120,520),90:(160,120),180:(880,160),270:(520,880)}
        with tempfile.TemporaryDirectory(dir=STAGE/'tests/.artifacts') as directory:
            for rotation,(x,y) in centers.items():
                writer=PdfWriter();page=writer.add_blank_page(width=600,height=400)
                page.cropbox=RectangleObject([50,30,550,370]);page[NameObject('/UserUnit')]=NumberObject(2)
                stream=DecodedStreamObject();stream.set_data(b'0 g 100 100 20 20 re f')
                page[NameObject('/Contents')]=writer._add_object(stream);page.rotate(rotation)
                path=Path(directory)/f'landmark-{rotation}.pdf'
                with path.open('wb') as output:writer.write(output)
                rendered=Image.open(io.BytesIO(render(path,1))).convert('L')
                self.assertEqual(rendered.size,(1000,680) if rotation%180==0 else (680,1000))
                self.assertLess(rendered.getpixel((x,y)),10)
                self.assertGreater(rendered.getpixel((x+30,y)),240)
                crop=Image.open(io.BytesIO(render(path,1,[x-25,y-25,x+25,y+25],2))).convert('L')
                self.assertEqual(crop.size,(100,100));self.assertLess(crop.getpixel((50,50)),10)
    def test_opposed_triangles_rotated_scaled_and_distinct_internal_mark(self):
        # The generator's independent object inventory is two plain outlines,
        # one marked lookalike. It never calls the production detector.
        import math
        paths=[]
        for i,(x,y,angle,scale,marked) in enumerate([(30,40,0,1,False),(120,60,37,1.6,False),(220,80,90,.8,True)]):
            def convert(pt):
                a=math.radians(angle);px,py=pt
                return [x+scale*(px*math.cos(a)-py*math.sin(a)),y+scale*(px*math.sin(a)+py*math.cos(a))]
            for j,pts in enumerate([[[-16,-16],[0,0],[-16,16],[-16,-16]],[[16,-16],[0,0],[16,16],[16,-16]]]):
                pts=list(map(convert,pts));bounds=[min(p[0] for p in pts),min(p[1] for p in pts),max(p[0] for p in pts),max(p[1] for p in pts)]
                paths.append({'id':f'p{i}-{j}','kind':'curve','points':pts,'bbox':bounds})
            if marked:
                paths.append({'id':'distinguishing-mark','kind':'curve','points':[[x-3,y],[x,y+3],[x+3,y],[x,y-3]],'bbox':[x-3,y-3,x+3,y+3]})
        found=match_vectors({'page':1,'paths':paths},builtin_templates()['gate_valve'])
        self.assertEqual(len(found),2)
        self.assertTrue(all(d['method']=='native_opposed_triangle_profile' for d in found))
    def test_rotations_have_independently_expected_corners(self):
        expected={0:[0,200],90:[0,0],180:[100,0],270:[200,100]}
        for rotation,corner in expected.items():
            g=geometry([10,20,110,220],[10,20,110,220],rotation)
            self.assertEqual(point(g['pdf_to_page'],10,20),corner)
            self.assertEqual(point(g['page_to_pdf'],*corner),[10,20])
    def test_crop_and_user_unit_round_trip(self):
        g=geometry([0,0,600,400],[50,30,550,370],0,2)
        self.assertEqual(point(g['pdf_to_page'],50,370),[0,0]);self.assertEqual(g['width'],1000)
    def test_determinism_or_high_confidence_never_grants_exact(self):
        scope={'source_hash':'a'};coverage={'complete':True,'classification_supported':True}
        self.assertEqual(count_label(scope,coverage,[{'score':1}]),'Estimated')
        self.assertEqual(count_label(scope,{'complete':False},[]),'Partial')
        self.assertEqual(count_label(scope,{'complete':True},[]),'Unverified')
        self.assertEqual(count_label(scope,coverage,[],{'published':True,'source_hash':'b'}),'Estimated')
    def test_duplicate_tiles_do_not_duplicate_symbols(self):
        findings=[{'id':'a','page':1,'class':'reference','bbox':[10,10,20,20],'score':.9},
                  {'id':'b','page':1,'class':'reference','bbox':[10.1,10,20,20],'score':.95},
                  {'id':'c','page':2,'class':'reference','bbox':[10,10,20,20],'score':.9}]
        self.assertEqual(len(deduplicate(findings)),2)
    def test_independent_matching_penalizes_misses_and_wrong_classes(self):
        truth=[{'page':1,'bbox':[0,0,10,10],'class':'gate'},{'page':1,'bbox':[20,0,30,10],'class':'check'}]
        result=evaluate([{'page':1,'bbox':[0,0,10,10],'class':'check'}],truth)
        self.assertEqual(result['recall'],.5);self.assertEqual(result['classification_accuracy'],0)
        self.assertEqual(result['ground_truth_status'],'not_independently_verified')
    def test_units_and_nts_are_guarded(self):
        self.assertEqual(convert('1','ft','mm'),'304.8')
        with self.assertRaises(ValueError):measurement(10,{'verified':True,'source_ids':['a'],'page':1,'units_per_point':2},{'page':1,'not_to_scale':True})
    def test_crossing_lines_do_not_create_connections(self):
        page={'page':1,'paths':[{'id':'a','kind':'line','points':[[0,5],[10,5]],'bbox':[0,5,10,5]},
                              {'id':'b','kind':'line','points':[[5,0],[5,10]],'bbox':[5,0,5,10]}]}
        graph=build(page);self.assertEqual(len(graph['crossings']),1)
        result=trace(graph,graph['edges'][0]['a'],graph['edges'][1]['b'])
        self.assertEqual(result['status'],'unresolved')


class DrawingChatTests(unittest.TestCase):
    setUp=fixtures.Checks.setUp;tearDown=fixtures.Checks.tearDown
    upload=fixtures.Checks.upload;send=fixtures.Checks.send;rows=fixtures.Checks.rows
    def test_count_chat_and_review_cannot_automatically_become_exact(self):
        f=self.upload('drawing.pdf',drawing(),'application/pdf')
        events=self.send('Count all gate valves in drawing.pdf.',use_files=True,file_ids=[f['id']])
        self.assertEqual(events[-1]['status'],'completed',events)
        self.assertIn('**Estimated: 2**',self.rows()[-1]['content'])
        tools=self.rows()[-1]['tool_evidence'];self.assertTrue(tools)
        evidence=self.client.get(f'/conversations/{self.cid}/tool-evidence/{tools[0]["id"]}').json()
        self.assertEqual(len(evidence['result']['detections']),2)
        self.assertIsNone(evidence['result']['verification_id'])
        base=f'/conversations/{self.cid}/files/{f["id"]}/drawing'
        limited=self.client.post(base+'/count',json={'pages':[1],'template_id':'gate_valve','bbox':[70.,160.,120.,205.]})
        self.assertEqual(limited.status_code,200,limited.text);self.assertEqual(limited.json()['count'],1)
    def test_real_drawing_number_and_branch_followup_revision(self):
        path=STAGE.parent/'Docs/page_14.pdf'
        self.assertTrue(path.exists(),'Required real drawing fixture is missing.')
        f=self.upload('page_14.pdf',path.read_bytes(),'application/pdf')
        self.send('In page_14.pdf, what is the drawing number?',use_files=True,file_ids=[f['id']])
        self.assertIn('GP3201-G41-PA081-000008',self.rows()[-1]['content'])
        self.send('What is its REV. value?',use_files=True,file_ids=[f['id']])
        self.assertIn('**04**',self.rows()[-1]['content'])
        self.assertTrue(self.rows()[-1]['tool_evidence'])
        self.assertFalse([c for c in self.calls if c.get('stream')])
        self.send('What are the drawing number and revision of page_14.pdf?',use_files=True,file_ids=[f['id']])
        self.assertIn('GP3201-G41-PA081-000008',self.rows()[-1]['content'])
        self.assertIn('**04**',self.rows()[-1]['content'])
    def test_synthetic_title_block_beats_unrelated_revision_note(self):
        f=self.upload('unrelated.pdf',drawing(),'application/pdf')
        self.send('What is the revision of unrelated.pdf?',use_files=True,file_ids=[f['id']])
        self.assertIn('**07**',self.rows()[-1]['content']);self.assertNotIn('**99**',self.rows()[-1]['content'])
    def test_unavailable_symbol_is_not_text_count_or_zero(self):
        f=self.upload('drawing.pdf',drawing(),'application/pdf')
        self.send('How many check valves appear in drawing.pdf?',use_files=True,file_ids=[f['id']])
        answer=self.rows()[-1]['content'];self.assertIn('Unverified',answer);self.assertNotIn('**0**',answer)
    def test_cross_conversation_render_and_stale_review_rejected(self):
        f=self.upload('drawing.pdf',drawing(),'application/pdf');other=self.client.post('/conversations').json()['id']
        self.assertEqual(self.client.get(f'/conversations/{other}/files/{f["id"]}/drawing/render').status_code,404)
        base=f'/conversations/{self.cid}/files/{f["id"]}/drawing'
        result=self.client.post(base+'/analyze',json={'page':1});self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.client.get(base+'/render?page=1').headers['content-type'],'image/png')
        self.assertEqual(self.client.get(base+'/render?page=2').status_code,422)
        bad={'source_hash':'0'*64,'expected_revision':0,'reviewer':'reviewer',
             'scope':{'file_id':f['id'],'source_hash':'0'*64,'pages':[1],'bbox':None,'template_id':'gate_valve','template_version':1},
             'objects':[],'reviewed_cells':[],'independent':True,'exhaustive':True,'unresolved':[]}
        self.assertEqual(self.client.post(base+'/reviews/publish',json=bad).status_code,409)
