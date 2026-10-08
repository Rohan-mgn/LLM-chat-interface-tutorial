"""Reproducible CPU-only vector scan benchmark; no model or database changes."""
import argparse
from array import array
import gc
import json
import math
from pathlib import Path
import platform
import random
import statistics
import sys
import time


def size_of(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen: return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict): size += sum(size_of(k, seen) + size_of(v, seen) for k, v in value.items())
    elif isinstance(value, (list, tuple)): size += sum(size_of(v, seen) for v in value)
    return size


def timing(samples):
    values = sorted(samples)
    return {"median_ms": round(statistics.median(values)*1000, 3),
            "p95_ms": round(values[math.ceil(.95*len(values))-1]*1000, 3) if len(values)>=20 else None}


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--repetitions', type=int, default=20)
    parser.add_argument('--output', type=Path)
    args=parser.parse_args()
    if args.repetitions<3: parser.error('At least three repetitions are required.')
    results=[]
    for count in (100, 1000, 4000):
        rng=random.Random(90210)
        def vector():
            v=[rng.uniform(-1,1) for _ in range(768)]
            norm=math.sqrt(sum(x*x for x in v))
            return [x/norm for x in v]
        query=vector()
        raw={str(i):json.dumps(vector()) for i in range(count)}
        cache={key:array('f', json.loads(v)) for key,v in raw.items()}
        baseline=[]; cold=[]; warm=[]; same=True; error=0
        for _ in range(args.repetitions):
            start=time.perf_counter()
            scores={key:sum(x*y for x,y in zip(query,json.loads(v))) for key,v in raw.items()}
            baseline.append(time.perf_counter()-start)
            start=time.perf_counter()
            fresh={key:array('f',json.loads(v)) for key,v in raw.items()}
            cold_scores={key:sum(x*y for x,y in zip(query,v)) for key,v in fresh.items()}
            cold.append(time.perf_counter()-start)
            start=time.perf_counter()
            cached={key:sum(x*y for x,y in zip(query,v)) for key,v in cache.items()}
            warm.append(time.perf_counter()-start)
            top=lambda s: sorted(s,key=lambda key:(-s[key],key))[:30]
            same &= top(scores)==top(cached)
            error=max(error,max(abs(scores[k]-cached[k]) for k in scores))
        results.append({'vectors':count,'dimensions':768,'repetitions':args.repetitions,
            'json_scan':timing(baseline),'cold_build_and_scan':timing(cold),'warm_scan':timing(warm),
            'cache_bytes_including_container_overhead':size_of(cache),
            'top30_order_identical':same,'max_float32_score_error':error})
        print(json.dumps(results[-1]),flush=True)
        del raw,cache;gc.collect()
    report={'python':sys.version,'platform':platform.platform(),'processor':platform.processor(),
            'scope':'Synthetic CPU scans; excludes SQLite IO and model inference. No end-to-end speedup implied.',
            'results':results}
    if args.output: args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')

if __name__=='__main__':main()
