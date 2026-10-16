"""Resolve references from completed ancestor operations, never sibling replies."""
from contextlib import closing
import copy
import json
import re
from .evidence import needs_reference
from .answer_references import requested, select

def previous_reply(docs, job):
    with closing(docs.connect()) as db:
        row=db.execute("""
            WITH RECURSIVE chain(id,parent_id,role,status,depth) AS (
                SELECT id,parent_id,role,status,0 FROM messages WHERE id=? AND conversation_id=?
                UNION ALL SELECT m.id,m.parent_id,m.role,m.status,c.depth+1
                FROM messages m JOIN chain c ON m.id=c.parent_id
                WHERE m.conversation_id=? AND c.depth<1000)
            SELECT m.id,m.content,r.details FROM chain c JOIN messages m ON m.id=c.id
            LEFT JOIN rag_runs r ON r.message_id=c.id
            WHERE c.role='assistant' AND c.status='completed' ORDER BY c.depth LIMIT 1
        """,(job["user_message_id"],job["conversation_id"],job["conversation_id"])).fetchone()
    if row is None:return None
    reply=dict(row)
    operation=json.loads(reply["details"]).get("operation") if reply["details"] else None
    reply["operation"]=operation if operation and operation.get("version")==1 else None
    return reply


def previous_operation(docs, job):
    reply=previous_reply(docs,job)
    return reply["operation"] if reply else None


def validate_reference_sources(docs,cid,mid,source_ids,selected):
    allowed={f["id"] for f in selected}
    sources=[]
    with closing(docs.connect()) as db:
        for sid in source_ids:
            row=db.execute("""SELECT c.file_id,c.text,c.metadata,f.original_filename FROM citations r
                JOIN chunks c ON c.id=r.chunk_id JOIN files f ON f.id=c.file_id
                WHERE r.message_id=? AND c.id=? AND f.conversation_id=?""",
                (mid,sid,cid)).fetchone()
            if row:
                source={**dict(row),"metadata":json.loads(row["metadata"])}
            else:
                row=db.execute("""SELECT b.file_id,b.data,f.original_filename FROM block_citations r
                    JOIN document_blocks b ON b.id=r.block_id JOIN files f ON f.id=b.file_id
                    WHERE r.message_id=? AND b.id=? AND f.conversation_id=?""",
                    (mid,sid.replace("SOURCE_","BLOCK_"),cid)).fetchone()
                if row:
                    block=json.loads(row["data"])
                    source={"file_id":row["file_id"],"text":block["text"],
                            "metadata":{k:v for k,v in block.items() if k!="text"},
                            "original_filename":row["original_filename"]}
            if row is None or row["file_id"] not in allowed:
                raise ValueError("A source cited by that earlier point is unavailable or no longer selected. Please ask using the current documents.")
            sources.append({**source,"id":sid,"score":1})
    return sources


def record(route, files, question, tools=None):
    return {"version":1,"route":route.model_dump(),"question":question,
            "files":[{"id":f["id"],"fingerprint":f["sha256"],"name":f["original_filename"]} for f in files],
            "tools":[{"file_id":t["file_id"],"operation":t["operation"],"arguments":t["arguments"]} for t in tools or []]}

def resolve(docs,job,question,files):
    reference=requested(question)
    if reference and any(f["original_filename"].casefold() in question.casefold() for f in files) and not re.search(r"\b(answer|reply)\b",question,re.I):
        reference=None  # An explicit filename identifies document structure.
    if not reference and not needs_reference(question): return None
    reply=previous_reply(docs,job)
    old=reply["operation"] if reply else None
    if not old:
        if reference:
            raise ValueError("That answer has no completed document operation to reuse. Please identify the point and selected document.")
        if re.search(r"^(and in|what about|how about)\b",question,re.I):
            raise ValueError("Please restate the document question or calculation; there is no completed operation on this branch to reuse.")
        return None
    available={f["id"]:f for f in files}
    named=[f for f in files if f["original_filename"].casefold() in question.casefold()
           or re.search(r"\bdocument\s+"+re.escape(f["original_filename"].rsplit(".",1)[0])+r"\b",question,re.I)]
    selected=named or [available[f["id"]] for f in old["files"] if f["id"] in available]
    if not selected:
        raise ValueError("The previous operation's document is no longer selected. Select it or name a current document.")
    if not named and len(selected)!=len(old["files"]):
        raise ValueError("The previous operation used a different file selection. Clarify which selected documents to use.")
    for f in old["files"]:
        if any(n["original_filename"]==f["name"] and n["id"]!=f["id"] for n in selected):
            raise ValueError("The previous document was replaced. Specify the operation for the current file.")
        if f["id"] in available and f["fingerprint"]!=available[f["id"]]["sha256"]:
            raise ValueError("The previous document changed. Specify the operation again for the current file.")
    result=copy.deepcopy(old);result["selected_ids"]=[f["id"] for f in selected]
    route=result["route"]["route"]
    if reference:
        if route not in ("DOCUMENT_QA","SUMMARIZE_DOCUMENT","SUMMARIZE_SECTION","COMPARE_DOCUMENTS"):
            raise ValueError("Please specify the exact calculation or count to revisit; its result will be recalculated from the source.")
        point=select(reply["content"],reference)
        result["reference_sources"]=validate_reference_sources(docs,job["conversation_id"],reply["id"],point["source_ids"],selected)
        result["answer_reference"]={**point,"message_id":reply["id"],"unverified":True}
        result["route"]={"route":"DOCUMENT_QA","section":old["route"].get("section","")}
        topic=re.sub(r"\[SOURCE_[^\]]+\]","",point["text"]).strip()
        result["query"]=topic+"\nFollow-up request: "+question
        return result
    if route=="TABLE_ANALYSIS":
        if len(old["tools"])!=1 or len(selected)!=1:
            raise ValueError("Which table operation and document should this follow-up use?")
        args=result["tools"][0]["arguments"]
        quarter=re.search(r"\bQ([1-4])\b",question,re.I)
        if quarter:
            filters=[f for f in args["filters"] if re.fullmatch(r"Q[1-4]",str(f["value"]),re.I)]
            if len(filters)!=1: raise ValueError("Specify which quarter column/filter to change.")
            filters[0]["value"]="Q"+quarter[1]
        elif not named:
            raise ValueError("Specify the new filter or document for the previous table calculation.")
        result["tools"][0]["file_id"]=selected[0]["id"]
    elif route in ("COUNT","EXACT_SEARCH","PAGE_SEARCH"):
        if not named: raise ValueError("Specify the document or exact term for this follow-up.")
    elif route=="SUMMARIZE_SECTION":
        result["route"]["route"]="DOCUMENT_QA"
        result["query"]=re.sub(r"\bits\b","the section\'s",question,flags=re.I)+" in section "+result["route"]["section"]
    elif route in ("COMPARE_DOCUMENTS","DOCUMENT_QA"):
        result["query"]=question+"\nPrevious question for reference: "+old["question"]
        if route=="COMPARE_DOCUMENTS":
            result["query"]=re.sub(r"^what about\s+","Compare ",question,flags=re.I)
    else: return None
    return result
