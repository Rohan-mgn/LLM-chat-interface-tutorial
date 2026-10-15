"""Exact operations over canonical blocks, with structural boundaries and provenance."""
import hashlib
import re
import unicodedata

WORD = re.compile(r"\w+(?:['\u2019]\w+)*", re.UNICODE)

def stable_blocks(fid, sections):
    result=[]
    for index,section in enumerate(sections):
        text=unicodedata.normalize("NFC",section.get("text",""))
        kind=section.get("type","page_text" if section.get("page") else "paragraph")
        location={k:section[k] for k in ("page","section","heading","heading_level","paragraph","sheet","row","table","slide","line_start","line_end","shape") if k in section}
        import json
        key=json.dumps({"file":fid,"location":location,"index":index,"type":kind,"text":text},sort_keys=True,ensure_ascii=False)
        bid="BLOCK_"+hashlib.sha256(key.encode()).hexdigest()[:24]
        # A section/page is a parent, not permission to join unrelated paragraphs.
        parent="PARENT_"+hashlib.sha256((fid+json.dumps({k:v for k,v in location.items() if k not in ("paragraph","row")},sort_keys=True)).encode()).hexdigest()[:24]
        result.append({**section,"text":text,"id":bid,"parent_id":parent,"block_index":index,"type":kind,
            "start":0,"end":len(text),"logical_group":section.get("logical_group",bid)})
    return result

def logical_texts(blocks):
    groups=[]
    for block in blocks:
        if not block.get("text") or block.get("type") in ("unreadable","image_description"):
            continue
        group=block.get("logical_group",block["id"])
        previous=groups[-1] if groups else None
        can_join=(previous is not None and previous["group"]==group and
                  block.get("continues_previous") and block.get("type") not in ("heading","row","table_header") and
                  previous["blocks"][-1].get("type") not in ("heading","row","table_header") and
                  all(previous["blocks"][-1].get(k)==block.get(k) for k in ("page","section","sheet","row")))
        if can_join:
            separator="" if block.get("preserve_contiguous_lines") and previous["text"].endswith("\n") else "\n"
            previous["offsets"].append(len(previous["text"])+len(separator))
            previous["text"]+=separator+block["text"]
            previous["blocks"].append(block)
        else:
            groups.append({"group":group,"text":block["text"],"blocks":[block],"offsets":[0]})
    return groups

def folded(text,case_sensitive=False):
    output=[]; offsets=[]
    whitespace=False
    for index,char in enumerate(text):
        if char.isspace():
            if not whitespace:
                output.append(" "); offsets.append(index)
            whitespace=True
        else:
            whitespace=False
            for part in char if case_sensitive else char.casefold():
                output.append(part); offsets.append(index)
    return "".join(output),offsets

def find_text(blocks,text,case_sensitive=False,whole_word=False,limit=200):
    if not isinstance(text,str) or not text.strip():
        raise ValueError("Specify a nonempty search term.")
    needle,_=folded(unicodedata.normalize("NFC",text),case_sensitive)
    matches=[]; total=0
    for group in logical_texts(blocks):
        hay,offsets=folded(group["text"],case_sensitive)
        if whole_word:
            candidates=[(m.start(),m.end()) for m in WORD.finditer(hay) if m[0]==needle]
        else:
            candidates=[]
            start=0
            while (pos:=hay.find(needle,start))>=0:
                candidates.append((pos,pos+len(needle)));start=pos+len(needle)
        for start,end in candidates:
            total+=1
            if len(matches)<limit:
                a,b=offsets[start],offsets[end-1]+1
                # Locate every block intersected by a match in continuous text.
                locations=[]
                for block,base in zip(group["blocks"],group["offsets"]):
                    if b>base and a<base+len(block["text"]):
                        locations.append({k:block[k] for k in ("id","page","section","heading","paragraph","sheet","row","slide","line_start","line_end") if k in block} |
                                         {"start":max(0,a-base),"end":min(len(block["text"]),b-base)})
                matches.append({"text":group["text"][a:b],"locations":locations,
                    "context":group["text"][max(0,a-80):min(len(group["text"]),b+80)]})
    return {"term":text,"count":total,"matches":matches,"truncated":total>len(matches),
            "case_sensitive":case_sensitive,"whole_word":whole_word}

def count_word(blocks,word,case_sensitive=False):
    return find_text(blocks,word,case_sensitive,True)

def count_phrase(blocks,phrase,case_sensitive=False):
    return find_text(blocks,phrase,case_sensitive)

def find_exact_phrase(blocks,phrase,case_sensitive=False):
    return find_text(blocks,phrase,case_sensitive)

def find_pages_containing(blocks,text,case_sensitive=False):
    result=find_text(blocks,text,case_sensitive,limit=100000)
    result["pages"]=sorted({loc["page"] for m in result["matches"] for loc in m["locations"] if "page" in loc})
    if not any("page" in b for b in blocks):
        raise ValueError("This file has no page numbers. Use section search instead.")
    result["matches"]=result["matches"][:200]
    return result

def get_page(blocks,page):
    found=[b for b in blocks if b.get("page")==page]
    if not found:raise ValueError("That page is unavailable.")
    return found

def get_slide(blocks,slide):
    if type(slide) is not int or not 1<=slide<=200:raise ValueError("Invalid slide number.")
    found=[b for b in blocks if b.get("slide")==slide]
    if not found:raise ValueError("That slide is unavailable.")
    return found


def get_lines(blocks,start,end):
    if type(start) is not int or type(end) is not int or not 1<=start<=end or end-start>500:
        raise ValueError("Choose a valid range of at most 501 lines.")
    found=[]
    for block in blocks:
        left,right=block.get("line_start"),block.get("line_end")
        if left is None or right<start or left>end:continue
        a,b=max(start,left),min(end,right)
        text="".join(block["text"].splitlines(keepends=True)[a-left:b-left+1])
        found.append({**block,"text":text,"line_start":a,"line_end":b})
    if not found:raise ValueError("Those source lines are unavailable.")
    return found


def inspect_code(blocks):
    symbols={};imports=[];warnings=[]
    for block in blocks:
        for symbol in block.get("structure",{}).get("symbols",[]):
            symbols[(symbol["name"],symbol["line_start"],symbol["line_end"])]=symbol
        imports.extend(block.get("structure",{}).get("imports",[]))
        warnings.extend(block.get("warnings",[]))
    if not any(b.get("language") for b in blocks):raise ValueError("Selected content is not source code or structured text.")
    return {"symbols":list(symbols.values())[:1000],"imports":list(dict.fromkeys(imports))[:1000],
        "warnings":list(dict.fromkeys(warnings)),"execution":"Uploaded code was not executed."}


def list_sections(blocks):
    return list(dict.fromkeys(b.get("section","") for b in blocks if b.get("section")))

def list_headings(blocks):
    return [{"text":b.get("heading",b["text"]),"level":b.get("heading_level"),"block_id":b["id"]}
            for b in blocks if b.get("type")=="heading"]

def get_section(blocks,section):
    normalize=lambda value:re.sub(r"^section\s+", "", value.strip().casefold())
    exact=[b for b in blocks if normalize(b.get("section",""))==normalize(section) or normalize(b.get("heading",""))==normalize(section)]
    if not exact:raise ValueError("Section not found. Available sections: "+", ".join(list_sections(blocks)[:30]))
    return exact

def document_word_count(blocks):
    return sum(len(WORD.findall(g["text"])) for g in logical_texts(blocks))

def document_character_count(blocks):
    return sum(len(g["text"]) for g in logical_texts(blocks))

def document_statistics(blocks):
    return {"words":document_word_count(blocks),"characters":document_character_count(blocks),
            "blocks":len(blocks),"pages":len({b["page"] for b in blocks if b.get("page")}),
            "sections":len(list_sections(blocks))}

def extract_pattern(blocks,pattern):
    results={}
    for group in logical_texts(blocks):
        for match in re.finditer(pattern,group["text"],re.I):
            value=match[0].rstrip(".,;)")
            results.setdefault(value,[]).extend(b["id"] for b in group["blocks"])
    return [{"value":k,"block_ids":list(dict.fromkeys(v))} for k,v in results.items()]

def extract_emails(blocks):
    return extract_pattern(blocks,r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")

def extract_urls(blocks):
    return extract_pattern(blocks,r"https?://[^\s<>\"']+")

def run_tool(blocks,operation,args):
    if operation=="get_slide":return get_slide(blocks,args.get("slide"))
    if operation=="get_lines":return get_lines(blocks,args.get("line_start"),args.get("line_end"))
    if operation=="inspect_code":return inspect_code(blocks)
    options={"case_sensitive":bool(args.get("case_sensitive",False))}
    term=args.get("term","")
    if operation=="count_word":return count_word(blocks,term,**options)
    if operation=="count_phrase":return count_phrase(blocks,term,**options)
    if operation in ("find_text","find_exact_phrase"):return find_text(blocks,term,**options)
    if operation=="find_pages_containing":return find_pages_containing(blocks,term,**options)
    if operation=="get_page":return {"blocks":get_page(blocks,int(args.get("page",0)))}
    if operation=="get_section":return {"blocks":get_section(blocks,str(args.get("section","")))}
    if operation=="list_sections":return {"sections":list_sections(blocks)}
    if operation=="list_headings":return {"headings":list_headings(blocks)}
    if operation=="document_word_count":return {"count":document_word_count(blocks)}
    if operation=="document_character_count":return {"count":document_character_count(blocks)}
    if operation=="document_statistics":return document_statistics(blocks)
    if operation=="extract_emails":return {"items":extract_emails(blocks)}
    if operation=="extract_urls":return {"items":extract_urls(blocks)}
    raise ValueError("Unsupported document operation.")
