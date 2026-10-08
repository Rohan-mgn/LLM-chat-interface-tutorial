"""Evidence assessment is separate from rank fusion and MMR selection."""
import re

POLICY_VERSION="evidence-v2"
STOP=set("the a an is are was were what which who when where how why of on in to and or for did do does it this that its my your please tell me about say said explain document documents file files according".split())

def normalized_term(term):
    if len(term)>4 and term.endswith("s") and not term.endswith(("ss","is","us")) and term.isalpha():
        return term[:-1]
    return term

def terms(text):
    # Keep exact identifiers as well as their components, including underscores.
    raw=re.findall(r"[^\W]+(?:[-.:/][^\W]+)*",text.casefold())
    return list(dict.fromkeys(normalized_term(t) for t in raw if t not in STOP))[:24]

def signals(query, text):
    wanted=terms(query); present={normalized_term(t) for t in re.findall(r"[^\W]+(?:[-.:/][^\W]+)*",text.casefold())}
    matched=[t for t in wanted if t in present]
    identifiers=[t for t in wanted if any(c.isdigit() for c in t) or "_" in t or "-" in t]
    return {"lexical_overlap":len(matched)/max(1,len(wanted)), "matched_terms":matched,
            "query_terms":wanted, "identifier_match":bool(identifiers) and all(t in present for t in identifiers)}

def needs_reference(question):
    return bool(re.search(r"\b(it|its|they|their|those|these|that|previous|above|same)\b|^(?:and\b|what about\b|how about\b)",question,re.I))

def choose_mode(question, comparison=False):
    if comparison or re.search(r"\b(compare|contrast|across|conflict|relationship|trade.?offs|connect|combined|versus)\b",question,re.I):
        return "DEEP"
    if needs_reference(question) or re.search(r"\b(why|explain|implications|limitations|analy[sz]e)\b",question,re.I) or len(terms(question))>10:
        return "STANDARD"
    return "FAST"

def strong_fast(candidates):
    # A conservative exact-coverage shortcut, not a universal cosine cutoff.
    if not candidates: return False
    top=candidates[0]
    wanted=top.get("query_terms",[])
    return (len(wanted)>=2 and top.get("lexical_overlap",0)==1
            and top.get("lexical_rank")==1 and top.get("dense_rank")==1
            and not any(r.get("lexical_overlap",0)==1 and r["text"]!=top["text"] for r in candidates[1:]))
    
def assess(candidates):
    accepted=[]
    for row in candidates:
        # A validated useful/direct rerank overrides weak raw scores.
        if "relevance" in row:
            useful=row["relevance"]>=2
        else:
            wanted=row.get("query_terms",[])
            matched=row.get("matched_terms",[])
            # Identifiers and multi-term coverage remain useful without embeddings.
            useful=bool(row.get("identifier_match") or (len(wanted)==1 and matched)
                        or (len(matched)>=2 and len(matched)*2>=len(wanted))
                        or (bool(matched) and row.get("dense_rank")==1 and row.get("lexical_rank")==1))
        if useful: accepted.append(row)
    return accepted, {"answerable":bool(accepted),"reason":"supported candidates" if accepted else "no supported evidence",
                      "policy":POLICY_VERSION}

def task_instructions(mode, question):
    if mode=="DEEP":
        return "\nAnswer the requested comparison or reasoning question directly. Distinguish each document, agreements, conflicts and missing evidence. Label interpretations; cite factual premises. Do not infer that silence means disagreement."
    if re.search(r"\b(explain|why|detail|limitations)\b",question,re.I):
        return "\nExplain the requested point using supported premises. Separate documented observations from interpretation and mention material evidence gaps."
    return "\nGive a concise direct answer. Include only relevant document facts with citations. If the requested fact is absent, say so; do not substitute a related fact."
