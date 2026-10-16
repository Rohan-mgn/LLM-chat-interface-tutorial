"""Application-owned attachment tools for the separately qualified planner.

No production routing switch is made here. Every call validates the original
turn revision, aliases and arguments; only SQLite-authoritative passages enter
the evidence ledger. The caller must use that same revision for SSE release.
"""
from contextlib import closing
import json
import re
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from starlette.concurrency import run_in_threadpool
from .planner import Tool
from .answering import canonical_sources, coverage_manifest
from .context_budget import estimate, input_limit, evidence_passage
from .document_tools import get_section, get_page, get_slide, get_lines, inspect_code
from .table_tools import tables_from_blocks, analyze
from .summarizer import Summarizer
from .evidence_release import SourceInvalidated
from .request_state import current

class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

class FileArgument(Arguments):
    file: str = Field(min_length=1, max_length=8)

class ReadArgument(FileArgument):
    view: Literal["full", "page", "section", "slide", "lines", "outline"]
    locator: str = Field(max_length=200)

class SearchArgument(Arguments):
    files: list[str] = Field(min_length=1, max_length=40)
    query: str = Field(min_length=1, max_length=400)

class ProcessArgument(Arguments):
    files: list[str] = Field(min_length=1, max_length=40)

class Filter(Arguments):
    column: str = Field(min_length=1, max_length=200)
    op: Literal["eq", "ne", "contains", "gt", "gte", "lt", "lte"]
    value: str | float | int

class Aggregate(Arguments):
    column: str | None
    op: Literal["sum", "average", "min", "max", "count"]

class CalculateArgument(FileArgument):
    table: str = Field(min_length=1, max_length=200)
    group_by: list[str] = Field(max_length=3)
    filters: list[Filter] = Field(max_length=10)
    aggregates: list[Aggregate] = Field(min_length=1, max_length=8)

class AttachmentTools:
    def __init__(self, docs, rag, provider, revision, objective, *, progress=lambda _:None,
                 deadline=None):
        self.docs, self.rag, self.provider = docs, rag, provider
        self.revision, self.objective = revision, objective
        self.cid = revision.conversation_id
        self.progress, self.deadline = progress, deadline
        self.aliases = {f"F{i+1}": row[0] for i,row in enumerate(revision.files)}
        self.sources, self.provenance, self.coverage = {}, [], []
        self.summary_calls = 0
        self.validate_scope()

    def validate_scope(self):
        self.revision.validate(self.docs)

    def _file(self, alias):
        if alias not in self.aliases:
            raise ValueError("File alias is outside this turn's authorized selection.")
        row = self.docs.row(self.cid, self.aliases[alias])
        if row is None:
            raise SourceInvalidated()
        return row

    def _blocks(self, alias):
        row = self._file(alias)
        if row.get("parse_state") not in ("ready", "partial"):
            raise ValueError("This attachment has no usable extracted content.")
        return row, self.docs.blocks(self.cid, row["id"])

    def _validate(self, model, args, eligible):
        result = model.model_validate(args).model_dump()
        aliases = result.get("files", [result["file"]] if "file" in result else [])
        if len(aliases) != len(set(aliases)) or any(a not in eligible for a in aliases):
            raise ValueError("Unsupported or unauthorized attachment.")
        if "query" in result and not result["query"].strip():
            raise ValueError("Search query is empty.")
        return result

    def _tool(self, name, description, model, eligible, operation, timeout=30):
        schema = model.model_json_schema()
        if "file" in schema["properties"]:
            schema["properties"]["file"]["enum"] = list(eligible)
        if "files" in schema["properties"]:
            schema["properties"]["files"]["items"]["enum"] = list(eligible)
            schema["properties"]["files"]["uniqueItems"] = True
        validate = lambda args: self._validate(model,args,eligible)
        async def execute(args):
            # Validate again even if called without Planner.
            self.validate_scope()
            args = validate(args)
            result, sources, provenance, coverage = await operation(args)
            self.validate_scope()
            if estimate(json.dumps(result,ensure_ascii=False)) > min(3500,input_limit(512)//3):
                raise ValueError("Tool result exceeds the evidence budget; narrow the read or process the document.")
            # Publish evidence only after successful validation, never on failure.
            for source in sources:
                self.sources[source["id"]] = source
            self.provenance.extend(provenance)
            self.coverage.extend(coverage)
            return result
        return Tool(name,description,schema,validate,execute,timeout)

    def catalog(self):
        self.validate_scope()
        readable, tables, code = [], [], []
        for alias in self.aliases:
            row = self._file(alias)
            if row.get("parse_state") not in ("ready","partial"):
                continue
            blocks = self.docs.blocks(self.cid,row["id"])
            if not any(b.get("text") and b.get("type") != "unreadable" for b in blocks):
                continue
            readable.append(alias)
            if tables_from_blocks(blocks): tables.append(alias)
            if any(b.get("language") for b in blocks): code.append(alias)
        tools = []
        if readable:
            tools += [
                self._tool("read_document","Read a full small document, page, section, slide, line range or outline.",
                           ReadArgument,readable,self._read),
                self._tool("search_documents","Retrieve targeted factual evidence; not whole-document coverage.",
                           SearchArgument,readable,self._search,90),
                self._tool("process_document","Process all readable sections for the original comprehensive objective.",
                           ProcessArgument,readable,self._process,900)]
        if tables:
            tools += [
                self._tool("inspect_table","Read actual table names, column names, row counts and bounded examples.",
                           FileArgument,tables,self._inspect_table),
                self._tool("calculate_table","Execute validated exact table aggregation and filters; never evaluate formulas.",
                           CalculateArgument,tables,self._calculate)]
        if code:
            tools.append(self._tool("inspect_code","Inspect source structure without executing uploaded code.",
                                    FileArgument,code,self._inspect_code))
        self.validate_scope()
        return tools

    def metadata(self):
        self.validate_scope()
        result=[]
        for alias in self.aliases:
            row=self._file(alias)
            blocks=self.docs.blocks(self.cid,row["id"])
            result.append({"alias":alias,"name":row["original_filename"],
                "state":row.get("parse_state"),"tokens":sum(estimate(b.get("text","")) for b in blocks),
                "format":row["original_filename"].rsplit(".",1)[-1].lower(),
                "coverage":json.loads(row.get("coverage") or "{}")})
        self.validate_scope()
        return result

    def _passages(self, file, blocks):
        # A sliced line range retains its public source identity. Its model text
        # comes directly from the canonical block; no vector-index text is used.
        return [{"id":b["id"].replace("BLOCK_","SOURCE_"),"file_id":file["id"],
            "original_filename":file["original_filename"],"text":b["text"],
            "metadata":{k:v for k,v in b.items() if k!="text"},"score":1}
            for b in blocks if b.get("text") and b.get("type")!="unreadable"]

    def _packet(self, sources):
        # Stable across this session's tool returns. Final evidence packing may
        # remap labels, and must use its returned source order for CitationFilter.
        ids=list(self.sources)
        for source in sources:
            if source["id"] not in ids:ids.append(source["id"])
        return [evidence_passage(s,ids.index(s["id"])+1) for s in sources]

    async def _read(self,args):
        def read():
            file,blocks=self._blocks(args["file"])
            view,locator=args["view"],args["locator"].strip()
            if view in ("page","slide"):
                if not re.fullmatch(r"[1-9][0-9]{0,2}",locator):
                    raise ValueError("A positive page/slide number is required.")
                blocks=(get_page if view=="page" else get_slide)(blocks,int(locator))
            elif view=="section": blocks=get_section(blocks,locator)
            elif view=="lines":
                match=re.fullmatch(r"([1-9][0-9]*)-([1-9][0-9]*)",locator)
                if not match: raise ValueError("Line locator must be start-end.")
                blocks=get_lines(blocks,int(match[1]),int(match[2]))
            elif locator:
                raise ValueError("A full read or outline does not accept a locator.")
            if view=="outline":
                locations=[]
                for b in blocks:
                    location={k:b[k] for k in ("page","slide","section","heading","sheet") if k in b}
                    if location and location not in locations:locations.append(location)
                return {"outline":locations,"coverage":"structure only; body text not read"},[],[],[]
            sources=self._passages(file,blocks)
            if not sources:raise ValueError("No readable content at that location.")
            manifest=coverage_manifest([file],sources)
            return {"passages":self._packet(sources),"scope":view,
                    "extraction":json.loads(file.get("coverage") or "{}")},sources,[],[manifest]
        return await run_in_threadpool(read)

    async def _search(self,args):
        ids=[self._file(a)["id"] for a in args["files"]]
        client=self.provider.client(45)
        try:
            sources,config=await self.rag.retrieve(client,self.cid,ids,args["query"],
                                                   mode="STANDARD",balanced=len(ids)>1)
        finally:await client.close()
        # The retrieval adapter already hydrates SQLite. Validate identity AND
        # authoritative content again before exposing any candidate to a planner.
        verified=[]
        files={self._file(a)["id"]:self._file(a) for a in args["files"]}
        canonical=None
        with closing(self.docs.connect()) as db:
            for source in sources:
                if source["file_id"] not in ids: raise SourceInvalidated()
                row=db.execute("SELECT text,metadata FROM chunks WHERE id=? AND file_id=?",
                               (source["id"],source["file_id"])).fetchone()
                if row is not None:
                    if row["text"]!=source["text"]:raise SourceInvalidated()
                    authoritative={"text":row["text"],"metadata":json.loads(row["metadata"]),
                        "original_filename":files[source["file_id"]]["original_filename"]}
                else:
                    # Parent expansion uses canonical SOURCE_ identities.
                    if canonical is None:
                        canonical={s["id"]:s for s in canonical_sources(self.docs,self.cid,list(files.values()))}
                    match=canonical.get(source["id"])
                    if not match or match["file_id"]!=source["file_id"] or match["text"]!=source["text"]:
                        raise SourceInvalidated()
                    authoritative={k:match[k] for k in ("text","metadata","original_filename")}
                verified.append({k:v for k,v in {**source,**authoritative}.items() if k!="vector"})
        sources=verified
        return {"passages":self._packet(sources),"coverage":"targeted evidence only",
                "no_evidence":not sources},sources,[],[]

    def _provenance(self,file,operation,args,result,blocks):
        return {"file_id":file["id"],"files":[file["id"]],"fingerprint":file["sha256"],
            "filename":file["original_filename"],"operation":operation,"arguments":args,
            "blocks_examined":len(blocks),"coverage":json.loads(file.get("coverage") or "{}"),
            "exact_result":result}

    async def _inspect_table(self,args):
        def inspect():
            file,blocks=self._blocks(args["file"])
            result={"tables":[{"name":t["name"],"columns":t["headers"],"rows":len(t["rows"]),
                "examples":[{"cells":r.get("cells"),"formulas":r.get("formulas",{})} for r in t["rows"][:3]],
                "examples_complete":len(t["rows"])<=3} for t in tables_from_blocks(blocks).values()]}
            return result,[],[],[]
        return await run_in_threadpool(inspect)

    async def _calculate(self,args):
        def calculate():
            file,blocks=self._blocks(args["file"])
            spec={k:v for k,v in args.items() if k!="file"}
            result=analyze(blocks,spec)
            provenance=self._provenance(file,"table_analysis",spec,result,blocks)
            return {"verified_calculation":result,"filename":file["original_filename"]},[],[provenance],[]
        return await run_in_threadpool(calculate)

    async def _inspect_code(self,args):
        def inspect():
            file,blocks=self._blocks(args["file"])
            result=inspect_code(blocks)
            return result,[],[self._provenance(file,"inspect_code",{},result,blocks)],[]
        return await run_in_threadpool(inspect)

    def _summary_call(self):
        # Count before inference so failed/cancelled attempts cannot reset the
        # ceiling. Leave four planner decisions and one final answer reserved.
        if self.summary_calls >= 59:
            raise ValueError("The shared complete-document model-call budget is exhausted.")
        state=current.get()
        if state and sum(v for k,v in state.calls.items() if k!="metadata") >= 63:
            raise ValueError("The request model-call ceiling is exhausted.")
        self.summary_calls += 1

    async def _process(self,args):
        files=[self._file(a) for a in args["files"]]
        documents,sources,info=await Summarizer(self.docs,self.provider).summarize(
            self.cid,files,self.progress,deadline=self.deadline,prior_calls=self.summary_calls,
            reserve_calls=5,final_budget=2200,task={"objective":self.objective},
            on_model_call=self._summary_call)
        self.summary_calls=info["calls"]
        return {"intermediate_notes":documents,"coverage":"all readable source blocks processed; answer coverage unverified"},sources,[],[coverage_manifest(files,sources)]

    def evidence(self):
        self.validate_scope()
        return {"sources":list(self.sources.values()),"tools":list(self.provenance),
                "coverage_manifests":list(self.coverage),"revision":self.revision.identity}
