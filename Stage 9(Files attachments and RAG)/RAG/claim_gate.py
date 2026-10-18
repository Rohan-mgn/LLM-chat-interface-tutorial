"""Pre-release source checks. Lexical consistency is not semantic verification."""
from contextlib import closing
import hashlib
import json
import re
from .evidence import terms
from .evidence_release import SourceInvalidated

VERSION = "pre-release-v1"
CITE = re.compile(r"\[(SOURCE_[a-f0-9]{24})\]")
NUMBER = re.compile(r"(?<!\w)[+-]?\d+(?:[.,]\d+)*(?:%|\b)")
QUANTITY = re.compile(NUMBER.pattern + r"|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|"
    r"billion|trillion|double|doubled|triple|tripled|twice|half|halved|quarter)\b", re.I)


def plain(value):
    return " ".join(re.sub(r"[*_`#>]", "", value).split()).strip()


def authoritative(docs, cid, sources):
    """Reload, rather than trusting a supplied passage or generated source span."""
    result = {}
    with closing(docs.connect()) as db:
        db.execute("BEGIN")
        for source in sources:
            row = db.execute("SELECT c.text,c.metadata,f.sha256 FROM chunks c JOIN files f ON f.id=c.file_id "
                "WHERE c.id=? AND c.file_id=? AND f.conversation_id=?",
                (source['id'], source['file_id'], cid)).fetchone()
            if row:
                text, metadata, fingerprint = row['text'], json.loads(row['metadata']), row['sha256']
            else:
                row = db.execute("SELECT b.data,f.sha256 FROM document_blocks b JOIN files f ON f.id=b.file_id "
                    "WHERE b.id=? AND b.file_id=? AND f.conversation_id=?",
                    (source['id'].replace('SOURCE_', 'BLOCK_'), source['file_id'], cid)).fetchone()
                if not row:
                    raise SourceInvalidated()
                block = json.loads(row['data'])
                text, metadata, fingerprint = block['text'], {k:v for k,v in block.items() if k!='text'}, row['sha256']
            if text != source['text']:
                raise SourceInvalidated()
            result[source['id']] = {**source, 'metadata':metadata, 'fingerprint':fingerprint}
    return result


def escape(value):
    return re.sub(r"([\\`*_{}\[\]()#+!|<>])", r"\\\1", value)


def excerpt_answer(sources, question):
    wanted = set(terms(question))
    ranked = []
    for source in sources.values():
        pieces = re.split(r"(?<=[.!?])\s+|\n", source['text'])
        # Preserve nearby names, definitions and qualifiers when a matched PDF
        # heading occupies a separate native-text line. Small passages stay whole.
        indices={i for i,piece in enumerate(pieces) if wanted & set(terms(piece))}
        neighbors={j for i in indices for j in (i-1,i,i+1) if 0<=j<len(pieces)}
        best=source['text'] if len(source['text'])<=650 else ' '.join(pieces[i] for i in sorted(neighbors)) or source['text']
        best=' '.join(best.split())
        if not best.strip():
            continue
        ranked.append((len(wanted & set(terms(best))), source, best[:650]))
    ranked.sort(key=lambda item:-item[0])
    if not ranked:
        return "The selected sources do not provide enough readable evidence to answer this question."
    lines = ["I couldn't verify the drafted answer. The available source evidence is:"]
    for _, source, passage in ranked[:6]:
        lines.append('> '+escape(passage)+' ['+source['id']+']')
    lines.append("These excerpts do not establish that every part of the question has been answered.")
    return '\n\n'.join(lines)


def release_answer(docs, cid, draft, result, question):
    sources = authoritative(docs, cid, result.get('sources', []))
    ledger, rejected, parts = [], [], []
    for paragraph in re.split(r'\n\s*\n', draft.strip()):
        if not paragraph.strip():
            continue
        ids = list(dict.fromkeys(CITE.findall(paragraph)))
        body = plain(CITE.sub('', paragraph))
        # Headings and list ordinals are formatting, not source facts.
        body = re.sub(r'^\d+[.)]\s+', '', body)
        record = {'text':body, 'source_ids':ids, 'support':'unresolved'}
        if not ids or any(sid not in sources for sid in ids):
            record['reason']='missing or invalid source'; rejected.append(record); continue
        passages = [plain(sources[sid]['text']) for sid in ids]
        observed = body.strip(' .,:;"“”')
        sentences=[plain(s).strip(' .,:;') for passage in passages for s in re.split(r'(?<=[.!?])\s+|\n',passage)]
        explicit=bool(observed) and any(observed.casefold()==s.casefold() for s in sentences)
        # Short named fragments can identify a cited passage, but numerical or
        # predicate-bearing fragments cannot drop source negations/conditions.
        named_fragment=not QUANTITY.search(body) and len(body.split())<=4 and not re.search(r'\b(is|are|was|were|has|have|can|will|must|not|never)\b',body,re.I)
        if named_fragment:explicit=explicit or any(observed.casefold() in p.casefold() for p in passages)
        if not explicit:
            # Do not validate a number merely because it appears somewhere in a passage.
            if QUANTITY.search(body) or re.search(r'\b(exact|verified|revision|diameter|height|width|length)\b', body, re.I):
                record['reason']='critical claim is not a source-exact statement'; rejected.append(record); continue
            quoted = re.findall(r'["“]([^"”]{4,})["”]', body)
            if any(not any(q in p for p in passages) for q in quoted):
                record['reason']='quotation mismatch'; rejected.append(record); continue
            wanted=set(terms(body)); present=set(terms(' '.join(passages)))
            if not wanted or len(wanted & present)/len(wanted)<.7:
                record['reason']='insufficient lexical support'; rejected.append(record); continue
            negatives={'not','never','no','without','cannot'}
            if (set(re.findall(r'\w+',body.lower())) & negatives) != (set(re.findall(r'\w+',' '.join(passages).lower())) & negatives):
                record['reason']='negation requires independent interpretation'; rejected.append(record); continue
        record.update(support='explicit' if explicit else 'inferred',
            evidence=[{'id':sid, 'fingerprint':sources[sid]['fingerprint'],
                       'location':sources[sid]['metadata']} for sid in ids])
        ledger.append(record)
        parts.append(paragraph if explicit else 'Interpretation of the cited passages: '+paragraph)
    fallback=bool(rejected) or not parts
    output=excerpt_answer(sources,question) if fallback else '\n\n'.join(parts)
    # The model may omit a conflicting value. Recompute from canonical passages,
    # then render the original values and source links before any output release.
    from .research import conflicts
    competing=conflicts(sources.values())
    if competing:
        notices=[]
        for conflict in competing[:8]:
            values=list(dict.fromkeys((v['value'],v['source_id']) for v in conflict['values']))
            text=escape(conflict['field'])+': '+ '; '.join(escape(value)+' ['+sid+']' for value,sid in values[:8])
            if len(values)>8:text+='; additional values omitted (partial conflict detail).'
            if conflict['status']=='incompatible_unit_dimensions':text+=' The units describe incompatible quantities.'
            notices.append(text)
        output+='\n\nCompeting source fields remain unresolved; their object, revision and applicability may differ:\n\n'+'\n\n'.join(notices)
        if len(competing)>8:output+='\n\nPartial conflict detail: additional competing fields are recorded in the evidence ledger.'
    research=result.get('debug',{}).get('research',{})
    coverage=research.get('supplied_coverage',research.get('coverage',{}))
    if coverage.get('partial') or coverage.get('complete') is False:
        gaps=[entry['question'] for entry in coverage.get('subquestions',[]) if not entry['source_ids']]
        notice='Partial evidence coverage: the two-round retrieval budget did not establish support for every requested point.'
        if gaps:notice+=' Uncovered requests: '+escape('; '.join(gaps))+'.'
        if coverage.get('missing_files'):notice+=' Evidence is missing for some selected documents.'
        output+='\n\n'+notice
    result['debug']['claim_gate']={'version':VERSION,'before_release':True,'claims':ledger,
        'rejected':rejected,'fallback':fallback,'field_conflicts':competing,'independent_semantic_verification':False,
        'output_sha256':hashlib.sha256(output.encode()).hexdigest(),
        'limitation':'Non-extractive narrative is explicitly an interpretation, not a verified fact.'}
    return output
