"""Document drafts must pass the evidence gate before their first user-visible delta."""
import unittest
from unittest.mock import patch
import check_rag as fixtures
from RAG.research import coverage,rank


class ClaimReleaseTests(unittest.TestCase):
    setUp=fixtures.Checks.setUp;tearDown=fixtures.Checks.tearDown
    upload=fixtures.Checks.upload;send=fixtures.Checks.send;rows=fixtures.Checks.rows
    def test_fabricated_numeric_claim_never_reaches_sse(self):
        f=self.upload(data=b'The project codename is Blue Falcon. The budget is 42 dollars.')
        original=fixtures.FakeModel.chat
        async def fabricated(client,**kwargs):
            if not kwargs.get('stream'):return await original(client,**kwargs)
            async def stream():
                yield {'message':{'content':'The budget is 999 dollars [S1].'}}
            return stream()
        with patch.object(fixtures.FakeModel,'chat',fabricated):
            events=self.send('What is the project budget?',use_files=True,file_ids=[f['id']])
        emitted=''.join(e.get('content','') for e in events if e['type']=='delta')
        self.assertNotIn('999',emitted);self.assertIn('42',emitted)
        self.assertEqual(events[-1]['status'],'completed')
    def test_missing_citations_fall_back_to_actual_passages(self):
        f=self.upload();original=fixtures.FakeModel.chat
        async def uncited(client,**kwargs):
            if not kwargs.get('stream'):return await original(client,**kwargs)
            async def stream():yield {'message':{'content':'The project is named Imaginary Eagle.'}}
            return stream()
        with patch.object(fixtures.FakeModel,'chat',uncited):self.send(use_files=True,file_ids=[f['id']])
        answer=self.rows()[-1];self.assertNotIn('Imaginary Eagle',answer['content']);self.assertIn('Blue Falcon',answer['content']);self.assertTrue(answer['sources'])

    def test_source_substring_cannot_remove_a_numeric_condition(self):
        f=self.upload(data=b'Only after safety approval, the budget is 42 dollars.')
        original=fixtures.FakeModel.chat
        async def conditional(client,**kwargs):
            if not kwargs.get('stream'):return await original(client,**kwargs)
            async def stream():yield {'message':{'content':'The budget is 42 dollars [S1].'}}
            return stream()
        with patch.object(fixtures.FakeModel,'chat',conditional):
            self.send('What is the budget?',use_files=True,file_ids=[f['id']])
        answer=self.rows()[-1]['content']
        self.assertIn('Only after safety approval',answer)
        self.assertIn("couldn't verify",answer)

    def test_omitted_competing_value_is_disclosed_before_sse_release(self):
        f=self.upload(data=b'Budget: 42 dollars\nBudget: 50 dollars')
        original=fixtures.FakeModel.chat
        async def one_value(client,**kwargs):
            if not kwargs.get('stream'):return await original(client,**kwargs)
            self.calls.append(kwargs)
            async def stream():yield {'message':{'content':'Budget: 42 dollars [S1].'}}
            return stream()
        with patch.object(fixtures.FakeModel,'chat',one_value):
            events=self.send('What is the budget?',use_files=True,file_ids=[f['id']])
        emitted=''.join(e.get('content','') for e in events if e['type']=='delta')
        self.assertIn('42 dollars',emitted);self.assertIn('50 dollars',emitted)
        self.assertIn('Competing source fields remain unresolved',emitted)
        self.assertEqual(emitted,self.rows()[-1]['content'])
        prompts=[c['messages'] for c in self.calls if c.get('stream')]
        self.assertTrue(any('Competing explicit field values exist' in m['content'] for p in prompts for m in p))

    def test_spelled_out_numbers_cannot_bypass_critical_value_gate(self):
        f=self.upload(data=b'The actual project budget has been approved at forty two dollars for the equipment.')
        original=fixtures.FakeModel.chat
        async def spelled_number(client,**kwargs):
            if not kwargs.get('stream'):return await original(client,**kwargs)
            async def stream():
                yield {'message':{'content':'The actual project budget has been approved at ninety nine dollars for the equipment [S1].'}}
            return stream()
        with patch.object(fixtures.FakeModel,'chat',spelled_number):
            events=self.send('What is the project budget?',use_files=True,file_ids=[f['id']])
        emitted=''.join(e.get('content','') for e in events if e['type']=='delta')
        self.assertNotIn('ninety nine',emitted);self.assertIn('forty two',emitted)


class ResearchTests(unittest.TestCase):
    def test_field_conflicts_normalize_units_but_not_revision_identifiers(self):
        from RAG.research import conflicts
        def source(sid,text):return {'id':sid,'file_id':sid,'text':text}
        self.assertEqual(conflicts([source('a','Height: 2 m'),source('b','Height: 200 cm')]),[])
        incompatible=conflicts([source('a','Height: 2 m'),source('b','Height: 2 kg')])
        self.assertEqual(incompatible[0]['status'],'incompatible_unit_dimensions')
        self.assertEqual([v['value'] for v in incompatible[0]['values']],['2 m','2 kg'])
        self.assertTrue(conflicts([source('a','Revision: 04'),source('b','Revision: 4')]))
        self.assertEqual(conflicts([source('a','Height: 2 m')]),[])
    def test_short_excerpt_preserves_byline_and_normalizes_layout_whitespace(self):
        from RAG.claim_gate import excerpt_answer
        source={'id':'SOURCE_example','text':'A Practical Manual\nDesign notes for practitioners\nLee\tChen and Morgan\tPatel'}
        answer=excerpt_answer({'SOURCE_example':source},'Who are the authors of A Practical Manual?')
        self.assertIn('Lee Chen and Morgan Patel',answer)

    def test_conceptual_contrast_routes_to_document_evidence(self):
        from RAG.document_agent import rules
        self.assertEqual(rules('Contrast local and global explanations; explain the caveats.').route,'DOCUMENT_QA')
    def test_subquestion_budget_is_disclosed_instead_of_dropping_requests(self):
        result=coverage('; '.join('Find topic '+str(i) for i in range(6)),[],['f'])
        self.assertEqual(len(result['subquestions']),6)
        self.assertEqual([s['status'] for s in result['subquestions'][4:]],['budget_unprocessed']*2)
        self.assertTrue(result['partial']);self.assertFalse(result['complete'])
    def test_gap_matrix_preserves_missing_subquestion_and_file(self):
        result=coverage('What is the budget?; What is the launch date?',[{'id':'a','file_id':'f','text':'The budget is 42.'}],['f','g'])
        self.assertFalse(result['complete']);self.assertEqual(result['missing_files'],['g'])
        self.assertEqual(result['subquestions'][1]['status'],'unanswered')
    def test_ranking_keeps_features_and_exact_identifier(self):
        rows=[{'id':'a','text':'The ordinary vessel.','metadata':{},'score':.1},
              {'id':'b','text':'Vessel TAG-742 revision 04.','metadata':{},'score':.01}]
        ranked=rank('TAG-742',rows);self.assertEqual(ranked[0]['id'],'b');self.assertTrue(ranked[0]['ranking_features']['identifier_match'])
