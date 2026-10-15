"""Compare frozen paired storage evaluations without retuning on held-out cases."""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import statistics


def retrieval(case):
    expected=set(case["expected_files"])
    if not expected or case.get("hit_at_k") is None:
        return None
    ranked=list(dict.fromkeys(case["ranked_files"]))[:30]
    hits=[name in expected for name in ranked]
    ideal=sum(1/math.log2(i+2) for i in range(min(len(expected),30)))
    return {
        "hit_at_k":int(any(hits)),
        "recall_at_k":len(expected.intersection(ranked))/len(expected),
        "mrr":next((1/(i+1) for i,hit in enumerate(hits) if hit),0),
        "ndcg":sum(int(hit)/math.log2(i+2) for i,hit in enumerate(hits))/ideal,
        "precision_at_returned":sum(hits)/len(ranked) if ranked else 0,
    }


def compare(baseline,candidate,threshold=.02,expected_cases=40):
    mismatches=[]
    for field in ("corpus_fingerprint","context_tokens","claim_review_mode","settings_fingerprint","processor","python","platform"):
        if not baseline.get(field) or baseline.get(field)!=candidate.get(field):
            mismatches.append(field)
    for model in ("llama3.2:3b","embeddinggemma:latest"):
        before=baseline.get("model_digests_at_start",{}).get(model)
        if not before or before!=candidate.get("model_digests_at_start",{}).get(model):
            mismatches.append("model:"+model)
    left={case["id"]:case for case in baseline["cases"]}
    right={case["id"]:case for case in candidate["cases"]}
    if (len(left)!=expected_cases or len(right)!=expected_cases or left.keys()!=right.keys()
            or len(left)!=len(baseline["cases"]) or len(right)!=len(candidate["cases"])):
        mismatches.append("complete unique paired cases")
    categories=defaultdict(list)
    for cid in left.keys()&right.keys():
        a,b=left[cid],right[cid]
        if any(a.get(key)!=b.get(key) for key in ("split","family","category","expected_files")):
            mismatches.append("case identity:"+cid)
        if a["status"]!="completed" or b["status"]!="completed":
            mismatches.append("incomplete response:"+cid)
        categories[a["category"]].append((a,b))
    if mismatches:
        return {"comparable":False,"passed":False,"mismatches":sorted(mismatches)}

    def group(pairs):
        result={}
        for name in ("hit_at_k","recall_at_k","mrr","ndcg","precision_at_returned"):
            paired=[(retrieval(a)[name],retrieval(b)[name]) for a,b in pairs if retrieval(a) is not None and retrieval(b) is not None]
            result[name]={"measured_cases":len(paired),"baseline":statistics.mean(x for x,_ in paired) if paired else None,
                "candidate":statistics.mean(y for _,y in paired) if paired else None,
                "delta":statistics.mean(y-x for x,y in paired) if paired else None}
        for name in ("total_ms","ttft_ms","model_calls"):
            paired=[(a.get(name),b.get(name)) for a,b in pairs if a.get(name) is not None and b.get(name) is not None]
            result[name]={"measured_cases":len(paired),
                "baseline_median":statistics.median(x for x,_ in paired) if paired else None,
                "candidate_median":statistics.median(y for _,y in paired) if paired else None}
        return result
    all_pairs=[(left[cid],right[cid]) for cid in sorted(left)]
    overall=group(all_pairs)
    by_category={key:group(pairs) for key,pairs in sorted(categories.items())}
    regressions=[name for name in ("hit_at_k","recall_at_k","mrr","ndcg")
                 if overall[name]["delta"] is None or overall[name]["delta"] < -threshold-1e-12]
    return {"comparable":True,"passed":not regressions,"paired_cases":len(all_pairs),
        "maximum_absolute_retrieval_regression":threshold,"regressions":regressions,
        "overall":overall,"categories":by_category,
        "limitations":[
            "Binary document-level relevance; not passage-level support or full answer correctness.",
            "nDCG is recomputed against all expected relevant files, including missing returned evidence.",
            "Only the frozen overall retrieval tolerance determines this comparison gate; categories remain visible.",
            "A single paired run under variable system load is not proof of a latency improvement.",
            "This report is not a complete migration acceptance attestation or assistant-quality verdict."
        ]}


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument("baseline",type=Path)
    parser.add_argument("candidate",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    threshold=json.loads((Path(__file__).parent/"vector_acceptance.json").read_text())["retrieval_max_absolute_regression"]
    report=compare(json.loads(args.baseline.read_text()),json.loads(args.candidate.read_text()),threshold)
    args.output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({key:report[key] for key in ("comparable","passed")}))
    return 0 if report["passed"] else 1


if __name__=="__main__":
    raise SystemExit(main())
