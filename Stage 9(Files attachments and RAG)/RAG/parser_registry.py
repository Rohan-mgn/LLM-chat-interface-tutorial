"""Validated parser capabilities; existing formats keep their representation IDs."""
from dataclasses import dataclass
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile

LEGACY_VERSION="structured-v2"
MAX_BYTES=10*1024*1024
MAX_OUTPUT=8*1024*1024
MAX_TEXT=500_000


@dataclass(frozen=True)
class ParserSpec:
    name: str
    mime: str
    version: str
    capabilities: tuple
    text: bool=False
    office_part: str=""


REGISTRY={
 ".txt":ParserSpec("Text","text/plain",LEGACY_VERSION,("text",),True),
 ".md":ParserSpec("Markdown","text/markdown",LEGACY_VERSION,("text","headings"),True),
 ".csv":ParserSpec("CSV","text/csv",LEGACY_VERSION,("text","tables","calculate"),True),
 ".pdf":ParserSpec("PDF","application/pdf",LEGACY_VERSION,("text","pages","optional_ocr","optional_vision")),
 ".docx":ParserSpec("DOCX","application/vnd.openxmlformats-officedocument.wordprocessingml.document",LEGACY_VERSION,("text","headings","tables"),office_part="word/document.xml"),
 ".xlsx":ParserSpec("XLSX","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",LEGACY_VERSION,("text","sheets","tables","calculate"),office_part="xl/workbook.xml"),
 ".pptx":ParserSpec("PPTX","application/vnd.openxmlformats-officedocument.presentationml.presentation","presentation-v1",("text","slides","notes","tables","chart_data","image_references"),office_part="ppt/presentation.xml"),
 ".png":ParserSpec("PNG","image/png",LEGACY_VERSION,("preview","optional_ocr","optional_vision")),
 ".jpg":ParserSpec("JPEG","image/jpeg",LEGACY_VERSION,("preview","optional_ocr","optional_vision")),
 ".jpeg":ParserSpec("JPEG","image/jpeg",LEGACY_VERSION,("preview","optional_ocr","optional_vision")),
 ".webp":ParserSpec("WebP","image/webp",LEGACY_VERSION,("preview","optional_ocr","optional_vision")),
}
for extension,mime in {".py":"text/x-python",".js":"text/javascript",".jsx":"text/javascript",
    ".ts":"text/typescript",".tsx":"text/typescript",".css":"text/css",
    ".json":"application/json",".xml":"application/xml",".yaml":"application/yaml",".yml":"application/yaml"}.items():
    REGISTRY[extension]=ParserSpec("Structured text" if extension in (".json",".xml",".yaml",".yml") else "Source code",
        mime,"source-lines-v1",("text","lines","code_structure" if extension==".py" else "structured_text"),True)
MIMES={key:spec.mime for key,spec in REGISTRY.items()}


def specification(extension):
    try:return REGISTRY[extension.lower()]
    except KeyError:raise ValueError("Unsupported attachment format.") from None


def archive_preflight(data,required):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos=archive.infolist()
            names=[item.filename for item in infos]
            if len(infos)>3000 or len(set(names))!=len(names) or sum(i.file_size for i in infos)>20*1024*1024:
                raise ValueError("Office archive exceeds safe expansion limits or has duplicate entries.")
            if required not in names:
                raise ValueError("The uploaded file is not the declared Office format.")
            for info in infos:
                name=info.filename.replace("\\","/")
                if name.startswith("/") or ".." in name.split("/") or ":" in name or info.flag_bits & 1:
                    raise ValueError("Unsafe or encrypted archive entry.")
                if "vbaproject" in name.casefold() or name.casefold().endswith((".exe",".dll",".vbs",".js")):
                    raise ValueError("Executable Office content is unsupported.")
    except zipfile.BadZipFile as error:
        raise ValueError("Invalid Office archive.") from error


def parse_local(path,extension):
    """Worker-only dispatch. Never execute uploaded source or follow relationships."""
    spec=specification(extension)
    if path.stat().st_size>MAX_BYTES:
        raise ValueError("Attachment exceeds parser input limit.")
    from .documents import decode_text,normalize
    if spec.office_part:
        archive_preflight(path.read_bytes(),spec.office_part)
    if extension==".pptx":
        from .structured_formats import presentation
        sections=presentation(path.read_bytes())
    elif spec.version=="source-lines-v1":
        from .structured_formats import source_sections
        sections=source_sections(path.read_bytes(),extension)
    else:
        from .document_parsers import native_sections
        sections=native_sections(path,extension,decode_text,normalize)
    if not isinstance(sections,list) or not sections or len(sections)>100_001:
        raise ValueError("Parser returned an invalid or oversized block set.")
    if any(not isinstance(b,dict) or not isinstance(b.get("text"),str) for b in sections):
        raise ValueError("Parser returned invalid source text.")
    if sum(len(b["text"]) for b in sections)>MAX_TEXT:
        raise ValueError("Document exceeds 500,000 extracted characters.")
    return sections


def extract_isolated(path,extension,timeout=30):
    """Terminable parser process; only a bounded JSON representation crosses back."""
    specification(extension)
    if not 0<timeout<=30:raise ValueError("Parser timeout must be between 0 and 30 seconds.")
    from .parser_limits import ParserLimit
    root=Path(__file__).resolve().parents[1]
    flags=getattr(subprocess,"CREATE_NO_WINDOW",0)
    # Windows venv python.exe is a redirector that may spawn another process.
    # Start the same base interpreter directly, adding this venv's packages, so
    # the single limited process is the actual parser rather than its launcher.
    command=[sys.executable,"-B","-m","RAG.parser_worker"]
    if sys.platform=="win32":
        bootstrap="import sys,runpy; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('RAG.parser_worker',run_name='__main__')"
        command=[sys._base_executable,"-B","-c",bootstrap,str(Path(sys.prefix)/"Lib"/"site-packages")]
    process=subprocess.Popen(command,cwd=root,
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=flags)
    limit=None
    try:
        # Worker waits on stdin until its OS limits are attached.
        limit=ParserLimit(process)
        request=json.dumps({"path":str(Path(path).resolve()),"extension":extension}).encode()
        output,_=process.communicate(request,timeout=timeout)
        if len(output)>MAX_OUTPUT or process.returncode:
            raise ValueError("Document parsing failed or exceeded its resource limits.")
        result=json.loads(output)
        if "error" in result:
            raise ValueError(result["error"])
        return result["sections"]
    except subprocess.TimeoutExpired as error:
        process.kill();process.communicate()
        raise ValueError(f"Document parsing exceeded its {timeout:g}-second time limit.") from error
    finally:
        if process.poll() is None:
            process.kill();process.communicate()
        if limit is not None:limit.close()
