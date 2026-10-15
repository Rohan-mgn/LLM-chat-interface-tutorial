"""Registry, native compatibility, bounded worker and new-format fixtures."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from RAG.documents import extract,decode_text,normalize
from RAG.document_parsers import native_sections
from RAG.parser_registry import specification,archive_preflight,extract_isolated,REGISTRY
from RAG.parser_limits import ParserLimit
from RAG.document_tools import stable_blocks,document_character_count,find_text,get_lines,get_slide,inspect_code
import check_rag as fixtures


def presentation_fixture():
    from RAG.structured_formats import NS
    p,a,r,c=NS["p"],NS["a"],NS["r"],NS["c"]
    relns="http://schemas.openxmlformats.org/package/2006/relationships"
    output=io.BytesIO()
    with zipfile.ZipFile(output,"w") as archive:
        archive.writestr("ppt/presentation.xml",f'<p:presentation xmlns:p="{p}" xmlns:r="{r}"><p:sldIdLst><p:sldId id="5" r:id="s2"/><p:sldId id="2" r:id="s1"/></p:sldIdLst></p:presentation>')
        archive.writestr("ppt/_rels/presentation.xml.rels",f'<Relationships xmlns="{relns}"><Relationship Id="s1" Type="{r}/slide" Target="slides/slide1.xml"/><Relationship Id="s2" Type="{r}/slide" Target="slides/slide2.xml"/></Relationships>')
        archive.writestr("ppt/slides/slide1.xml",f'<p:sld xmlns:p="{p}" xmlns:a="{a}"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Second in presentation order</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
        archive.writestr("ppt/slides/slide2.xml",f'''<p:sld xmlns:p="{p}" xmlns:a="{a}" xmlns:r="{r}" xmlns:c="{c}"><p:cSld><p:spTree>
          <p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>First slide title</a:t></a:r></a:p></p:txBody></p:sp>
          <p:graphicFrame><a:graphic><a:graphicData><a:tbl><a:tr><a:tc><a:txBody><a:p><a:r><a:t>Revenue</a:t></a:r></a:p></a:txBody></a:tc><a:tc><a:txBody><a:p><a:r><a:t>42</a:t></a:r></a:p></a:txBody></a:tc></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>
          <p:pic><p:nvPicPr><p:cNvPr descr="Architecture diagram"/></p:nvPicPr><p:blipFill><a:blip r:embed="image"/></p:blipFill></p:pic>
          <p:graphicFrame><c:chart r:id="chart"/></p:graphicFrame>
        </p:spTree></p:cSld></p:sld>''')
        archive.writestr("ppt/slides/_rels/slide2.xml.rels",f'''<Relationships xmlns="{relns}">
          <Relationship Id="notes" Type="{r}/notesSlide" Target="../notesSlides/notesSlide1.xml"/>
          <Relationship Id="chart" Type="{r}/chart" Target="../charts/chart1.xml"/>
          <Relationship Id="image" Type="{r}/image" Target="../media/image1.png"/>
          <Relationship Id="external" Type="{r}/hyperlink" Target="http://127.0.0.1:1/private" TargetMode="External"/>
        </Relationships>''')
        archive.writestr("ppt/notesSlides/notesSlide1.xml",f'<p:notes xmlns:p="{p}" xmlns:a="{a}"><a:p><a:r><a:t>Presenter notes explain the result.</a:t></a:r></a:p></p:notes>')
        archive.writestr("ppt/charts/chart1.xml",f'<c:chartSpace xmlns:c="{c}"><c:chart><c:ser><c:numRef><c:f>Sheet1!A1</c:f><c:numCache><c:pt idx="0"><c:v>42</c:v></c:pt></c:numCache></c:numRef></c:ser></c:chart></c:chartSpace>')
        archive.writestr("ppt/media/image1.png",b"Referenced only; never interpreted as image pixels.")
    return output.getvalue()


class ParserTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parent/".artifacts";root.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root)
        self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def file(self,ext,data):
        path=self.root/("fixture"+ext);path.write_bytes(data);return path

    def test_legacy_native_output_and_source_ids_remain_unchanged(self):
        for ext,data in ((".txt",b"One paragraph.\n\nAnother."),(".md",b"# Heading\n\nA paragraph."),(".csv",b"quarter,value\nQ3,12\nQ4,20")):
            path=self.file(ext,data)
            old=native_sections(path,ext,decode_text,normalize)
            result=extract(path,ext)
            self.assertEqual(result,old)
            self.assertEqual(stable_blocks("unchanged",result),stable_blocks("unchanged",old))
            self.assertEqual(specification(ext).version,"structured-v2")

    def test_python_ast_lines_whitespace_and_no_execution(self):
        marker=self.root/"should-not-exist"
        source="from pathlib import Path\nPath("+repr(str(marker))+").write_text('bad')\n\nclass Example:\n    def answer(self):\n        return 42\n"
        path=self.file(".py",source.encode())
        sections=extract(path,".py")
        self.assertFalse(marker.exists())
        self.assertEqual("".join(s["text"] for s in sections),source)
        blocks=stable_blocks("python",sections)
        result=inspect_code(blocks)
        self.assertEqual([s["name"] for s in result["symbols"]],["Example","answer"])
        self.assertEqual(get_lines(blocks,5,6)[0]["text"],"    def answer(self):\n        return 42\n")
        self.assertEqual(document_character_count(blocks),len(source))

    def test_continuous_source_boundary_exact_search_has_correct_offsets(self):
        source="\n".join(["# filler"]*79+["# neural","# network"])+ "\n"
        blocks=stable_blocks("code",extract(self.file(".py",source.encode()),".py"))
        self.assertEqual(len(blocks),2)
        self.assertEqual(document_character_count(blocks),len(source))
        match=find_text(blocks,"neural # network")
        self.assertEqual(match["count"],1)
        self.assertEqual(len(match["matches"][0]["locations"]),2)
        self.assertEqual(match["matches"][0]["locations"][-1]["start"],0)

    def test_json_and_syntax_errors_preserve_source_with_explicit_warning(self):
        sections=extract(self.file(".json",b'{"value":42}'),".json")
        self.assertEqual(sections[0]["structure"]["root_type"],"dict")
        bad=extract(self.file(".py",b"def broken(\n"),".py")
        self.assertIn("syntax",bad[0]["warnings"][0])
        yaml=extract(self.file(".yaml",b"name: value\n"),".yaml")
        self.assertIn("unavailable",yaml[0]["warnings"][0])

    def test_xml_external_entities_fail_closed(self):
        with self.assertRaises(ValueError):
            extract(self.file(".xml",b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///private">]><x>&secret;</x>'),".xml")

    def test_pptx_slide_order_notes_tables_cached_chart_and_visual_limitation(self):
        sections=extract(self.file(".pptx",presentation_fixture()),".pptx")
        self.assertEqual(sections[0]["text"],"First slide title")
        self.assertEqual(sections[-1]["text"],"Second in presentation order")
        self.assertTrue(any(s["type"]=="slide_notes" and s["slide"]==1 for s in sections))
        self.assertTrue(any(s["type"]=="table_text" and s["cells_text"]==["Revenue","42"] for s in sections))
        chart=next(s for s in sections if s["type"]=="chart_data")
        self.assertEqual(chart["text"],"42")
        self.assertEqual(chart["formulas"],["Sheet1!A1"])
        picture=next(s for s in sections if s["type"]=="image_reference")
        self.assertTrue(picture["requires_vision"])
        self.assertEqual(picture["origin"],"metadata")
        blocks=stable_blocks("slides",sections)
        self.assertTrue(all(b["slide"]==2 for b in get_slide(blocks,2)))
        self.assertEqual(stable_blocks("slides",sections),blocks)
        self.assertNotIn("http://127.0.0.1",json.dumps(sections))

    def test_archive_path_duplicate_macro_and_expansion_validation(self):
        for extra in ("../escape.xml","ppt/vbaProject.bin"):
            payload=io.BytesIO()
            with zipfile.ZipFile(payload,"w") as archive:
                archive.writestr("ppt/presentation.xml","<root/>");archive.writestr(extra,"bad")
            with self.assertRaises(ValueError):archive_preflight(payload.getvalue(),"ppt/presentation.xml")
        with self.assertRaises(ValueError):archive_preflight(presentation_fixture(),"word/document.xml")

    def test_worker_wall_timeout_is_terminable(self):
        with self.assertRaisesRegex(ValueError,"time|resource"):
            extract_isolated(self.file(".txt",b"tiny"),".txt",timeout=.001)

    @unittest.skipUnless(os.name=="nt","Windows Job Object qualification")
    def test_windows_job_enforces_memory_and_no_child_process(self):
        code="""import sys,subprocess
sys.stdin.read()
try:
    x=bytearray(600*1024*1024)
    print('unbounded')
except MemoryError: print('memory-bounded')
try:
    subprocess.run([sys.executable,'-c','pass'],check=True)
    print('spawned')
except OSError: print('child-blocked')
"""
        child=subprocess.Popen([sys._base_executable,"-c",code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW)
        limit=ParserLimit(child)
        try:
            out,err=child.communicate(b"go",timeout=10)
            self.assertEqual(child.returncode,0,err.decode())
            self.assertIn(b"memory-bounded",out);self.assertIn(b"child-blocked",out)
            self.assertNotIn(b"unbounded",out)
        finally:
            if child.poll() is None:child.kill();child.communicate()
            limit.close()


class UploadTests(unittest.TestCase):
    setUp=fixtures.Checks.setUp
    tearDown=fixtures.Checks.tearDown
    upload=fixtures.Checks.upload
    rows=fixtures.Checks.rows
    send=fixtures.Checks.send

    def test_pptx_upload_reports_missing_visual_coverage_and_slide_tools(self):
        file=self.upload("slides.pptx",presentation_fixture(),"application/vnd.openxmlformats-officedocument.presentationml.presentation")
        public=self.app.documents.public(file)
        self.assertIn("slides",public["capabilities"]["formats"])
        self.assertFalse(public["coverage"]["complete"])
        self.assertEqual(public["coverage"]["visual_uninterpreted"],[1])
        events=self.send("Show slide 2.",use_files=True,file_ids=[file["id"]])
        self.assertEqual(events[-1]["status"],"completed")
        self.assertIn("Second in presentation order",self.rows()[-1]["content"])

    def test_python_upload_line_tool_and_mime_validation(self):
        file=self.upload("code.py",b"def answer():\n    return 42\n","text/x-python")
        events=self.send("Show lines 1-2.",use_files=True,file_ids=[file["id"]])
        self.assertEqual(events[-1]["status"],"completed")
        self.assertIn("return 42",self.rows()[-1]["content"])
        response=self.client.post(f"/conversations/{self.cid}/files",files={"upload":("bad.py",b"valid text","image/png")})
        self.assertEqual(response.status_code,422)

if __name__=="__main__":unittest.main(verbosity=2)
