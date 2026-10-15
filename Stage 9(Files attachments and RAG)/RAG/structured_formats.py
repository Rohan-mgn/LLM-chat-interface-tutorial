"""Source lines and PresentationML extraction without executing document code."""
import ast
import io
import json
import posixpath
import re
import unicodedata
import zipfile
from defusedxml import ElementTree as ET

MAX_TEXT=500_000
R="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS={"p":"http://schemas.openxmlformats.org/presentationml/2006/main",
    "a":"http://schemas.openxmlformats.org/drawingml/2006/main","r":R,
    "c":"http://schemas.openxmlformats.org/drawingml/2006/chart"}


def source_sections(data,extension):
    text=data.decode("utf-16" if data.startswith((b"\xff\xfe",b"\xfe\xff")) else "utf-8-sig")
    text=unicodedata.normalize("NFC",text.replace("\r\n","\n").replace("\r","\n"))
    if any(ord(c)<32 and c not in "\n\t" for c in text) or len(text)>MAX_TEXT:
        raise ValueError("Invalid or oversized structured text.")
    details={};warnings=[]
    if extension==".py":
        try:
            root=ast.parse(text)
            nodes=list(ast.walk(root))
            if len(nodes)>100_000:raise ValueError("Source AST exceeds its node limit.")
            details["symbols"]=[{"name":n.name,"kind":type(n).__name__,"line_start":n.lineno,"line_end":n.end_lineno}
                for n in nodes if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
            details["imports"]=[ast.get_source_segment(text,n)[:400] for n in nodes if isinstance(n,(ast.Import,ast.ImportFrom))]
        except SyntaxError as error:
            warnings.append("Python syntax could not be parsed at line "+str(error.lineno)+". Raw source is preserved.")
    elif extension==".json":
        try:
            value=json.loads(text)
            details["root_type"]=type(value).__name__
            if isinstance(value,dict):details["keys"]=[str(k)[:120] for k in list(value)[:100]]
        except (ValueError,RecursionError):
            warnings.append("Invalid or excessively nested JSON; raw source is preserved.")
    elif extension==".xml":
        # Entities and external declarations fail closed; no external resource access.
        root=ET.fromstring(text)
        if sum(1 for _ in root.iter())>100_000:raise ValueError("XML exceeds its element limit.")
        details["root_tag"]=root.tag[:200]
    elif extension in (".yaml",".yml"):
        warnings.append("YAML is preserved as source text; YAML semantic parsing is unavailable.")
    else:
        details["analysis"]="Source lines only; no runtime execution or semantic AST is claimed."
    lines=text.splitlines(keepends=True)
    result=[]
    for start in range(0,len(lines),80):
        end=min(start+80,len(lines))
        symbols=[s for s in details.get("symbols",[]) if s["line_start"]<=end and s["line_end"]>=start+1]
        result.append({"text":"".join(lines[start:end]),"type":"code" if extension not in (".json",".xml",".yaml",".yml") else "structured_text",
            "origin":"native","language":extension[1:],"line_start":start+1,"line_end":end,
            "section":"Source","logical_group":"source-lines","continues_previous":start>0,"preserve_contiguous_lines":True,"warnings":warnings,
            "structure":{**{k:v for k,v in details.items() if k not in ("symbols","imports")},"symbols":symbols,
                "imports":details.get("imports",[])[:100] if start==0 else []}})
    return result


def relationship_map(archive,part):
    relpath=posixpath.join(posixpath.dirname(part),"_rels",posixpath.basename(part)+".rels")
    if relpath not in archive.namelist():return {}
    result={}
    for rel in ET.fromstring(archive.read(relpath)):
        if rel.get("TargetMode")=="External":continue
        target=rel.get("Target","")
        resolved=posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join(posixpath.dirname(part),target))
        if resolved.startswith("../") or ":" in resolved or "\\" in resolved or resolved not in archive.namelist():
            continue
        result[rel.get("Id")]=(resolved,rel.get("Type","").rsplit("/",1)[-1])
    return result


def paragraphs(element):
    return ["".join(p.itertext()) if p.tag.rsplit("}",1)[-1]=="t" else "".join(n.text or "" for n in p.findall(".//a:t",NS))
        for p in element.findall(".//a:p",NS)]


def presentation(data):
    output=[];total=0
    def add(item):
        nonlocal total
        total+=len(item["text"])
        if total>MAX_TEXT or len(output)>=10_000:raise ValueError("Presentation exceeds its extraction limits.")
        output.append(item)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root=ET.fromstring(archive.read("ppt/presentation.xml"))
        rels=relationship_map(archive,"ppt/presentation.xml")
        slides=root.findall("p:sldIdLst/p:sldId",NS)
        if not slides or len(slides)>200:raise ValueError("Presentations require 1-200 slides.")
        for number,item in enumerate(slides,1):
            relation=rels.get(item.get("{"+R+"}id"))
            if not relation or relation[1]!="slide":raise ValueError("Slide relationship is unavailable.")
            part=relation[0];slide=ET.fromstring(archive.read(part));links=relationship_map(archive,part)
            tree=slide.find("p:cSld/p:spTree",NS)
            if tree is None:
                add({"text":"","slide":number,"type":"unreadable","section":f"Slide {number}","origin":"native"})
                continue
            before=len(output)
            for ordinal,shape in enumerate(tree,1):
                texts=[t for t in paragraphs(shape) if t.strip()]
                placeholder=shape.find(".//p:ph",NS)
                is_title=placeholder is not None and placeholder.get("type") in ("title","ctrTitle")
                # Tables have explicit row boundaries and must not be duplicated as prose.
                table=shape.find(".//a:tbl",NS)
                if table is not None:
                    for rowno,row in enumerate(table.findall("a:tr",NS),1):
                        cells=["\n".join(paragraphs(cell)) for cell in row.findall("a:tc",NS)]
                        add({"text":" | ".join(cells),"type":"table_text","slide":number,"row":rowno,
                            "table":f"Slide {number} table {ordinal}","cells_text":cells,"section":f"Slide {number}","origin":"native"})
                elif texts:
                    add({"text":"\n".join(texts),"type":"heading" if is_title else "slide_text",
                        "heading_level":1 if is_title else None,"slide":number,"shape":ordinal,"section":f"Slide {number}","origin":"native"})
                for image in shape.findall(".//a:blip",NS):
                    media=links.get(image.get("{"+R+"}embed"))
                    props=shape.find(".//p:cNvPr",NS)
                    alt="" if props is None else props.get("descr") or props.get("title","")
                    add({"text":alt,"type":"image_reference","slide":number,"shape":ordinal,
                        "section":f"Slide {number}","origin":"metadata","requires_vision":True,
                        "media_reference":media[0] if media else None,
                        "warnings":["Image pixels have not been visually interpreted."]})
                for chartref in shape.findall(".//c:chart",NS):
                    link=links.get(chartref.get("{"+R+"}id"))
                    if not link or link[1]!="chart":continue
                    chart=ET.fromstring(archive.read(link[0]))
                    values=[n.text or "" for n in chart.findall(".//c:v",NS)]
                    formulas=[n.text or "" for n in chart.findall(".//c:f",NS)]
                    add({"text":" | ".join(values),"type":"chart_data","slide":number,"shape":ordinal,
                        "section":f"Slide {number}","origin":"cached_chart_data","formulas":formulas,
                        "warnings":["Cached chart values only; no formula evaluation, external workbook access, or visual interpretation."]})
            for target,kind in links.values():
                if kind=="notesSlide":
                    notes=ET.fromstring(archive.read(target))
                    text="\n".join(t for t in paragraphs(notes) if t.strip())
                    if text:add({"text":text,"type":"slide_notes","slide":number,"section":f"Slide {number} notes","origin":"native"})
            if len(output)==before:
                add({"text":"","type":"unreadable","slide":number,"section":f"Slide {number}","origin":"native"})
    return output
