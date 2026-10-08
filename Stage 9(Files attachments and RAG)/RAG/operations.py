"""Resolve references from completed ancestor operations, never sibling replies."""
from contextlib import closing
import copy
import json
import re
from .evidence import needs_reference

def previous_operation(docs, job):
    with closing(docs.connect()) as db:
        rows=db.execute("""
            WITH RECURSIVE chain(id,parent_id,role,status,depth) AS (
                SELECT id,parent_id,role,status,0 FROM messages WHERE id=? AND conversation_id=?
                UNION ALL SELECT m.id,m.parent_id,m.role,m.status,c.depth+1
                FROM messages m JOIN chain c ON m.id=c.parent_id
                WHERE m.conversation_id=? AND c.depth<1000)
            SELECT r.details FROM chain c LEFT JOIN rag_runs r ON r.message_id=c.id
            WHERE c.role='assistant' AND c.status='completed' ORDER BY c.depth LIMIT 1
        """,(job["user_message_id"],job["conversation_id"],job["conversation_id"])).fetchall()
    for row in rows:
        operation=json.loads(row[0]).get("operation") if row[0] else None
        if operation and operation.get("version")==1: return operation
    return None

def record(route, files, question, tools=None):
    return {"version":1,"route":route.model_dump(),"question":question,
            "files":[{"id":f["id"],"fingerprint":f["sha256"],"name":f["original_filename"]} for f in files],
            "tools":[{"file_id":t["file_id"],"operation":t["operation"],"arguments":t["arguments"]} for t in tools or []]}

def resolve(docs,job,question,files):
    if not needs_reference(question): return None
    old=previous_operation(docs,job)
    if not old:
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
