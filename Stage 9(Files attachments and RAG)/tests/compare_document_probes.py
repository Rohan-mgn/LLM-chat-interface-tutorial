"""Compare frozen probe results without representing term checks as human QA."""
import argparse
from collections import Counter
import json
from pathlib import Path


def summarize(report):
    calls=Counter();tokens=Counter();pages_found=pages_required=resolved=references=0;unmeasured_calls=[]
    for row in report['rows']:
        trace=row.get('trace',{})
        if row['events'][-1].get('status')=='completed' and trace.get('original_query',row['question'])==row['question']:
            calls.update(trace.get('model_calls',{}))
            for usage in trace.get('model_token_usage',[]):
                for key in ('prompt_eval_count','eval_count'):
                    if type(usage.get(key)) is int:tokens[key]+=usage[key]
        else:unmeasured_calls.append(row['question'])
        pages_found+=row.get('required_page_groups_found',0)
        pages_required+=row.get('required_page_groups',0)
        citations=row.get('citation_resolution',[])
        resolved+=sum(c['status']==200 for c in citations);references+=len(citations)
    return {'passed':report['passed'],'total':report['total'],
        'p50_seconds':report['p50_seconds'],'maximum_observed_seconds':max(r['seconds'] for r in report['rows']),
        'model_calls':dict(calls),'required_page_groups_found':pages_found,'required_page_groups':pages_required,
        'requests_without_attributable_call_metrics':unmeasured_calls,
        'reported_ollama_tokens':dict(tokens),'token_scope':'Reported calls only; missing token counts are unknown.',
        'peak_rss_bytes':None,
        'resolved_citations':resolved,'checked_citations':references,
        'failures':[r['question'] for r in report['rows'] if not r['passed']]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before',type=Path,required=True);parser.add_argument('--after',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    before=json.loads(args.before.read_text(encoding='utf8'));after=json.loads(args.after.read_text(encoding='utf8'))
    if not before['complete'] or not after['complete']:raise ValueError('Both evaluations must finish.')
    if before['fixture_sha256']!=after['fixture_sha256']:raise ValueError('Different frozen query fixtures.')
    if [(d['name'],d['sha256']) for d in before['ingestion']]!=[(d['name'],d['sha256']) for d in after['ingestion']]:raise ValueError('Different documents.')
    result={'same_fixture':True,'fixture_sha256':before['fixture_sha256'],'before':summarize(before),'after':summarize(after),
        'limitations':after['limitations'],'broad_answer_quality_acceptance':False,
        'human_reviewed_real_drawing_annotations':False,'latency_comparison':'Small single-observation development probes; concurrent offline regression may affect timings.'}
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf8');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
