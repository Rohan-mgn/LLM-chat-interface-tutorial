"""Record offline unittest results and verify runtime sources stayed unchanged."""
import argparse
import faulthandler
import hashlib
import json
from pathlib import Path
import platform
import sys
import time
import unittest

STAGE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(STAGE))


def hashes():
    paths=[*sorted((STAGE/"RAG").glob("*.py")),STAGE/"main.py",STAGE/"run.py",STAGE/"tree_store.py"]
    return {str(p.relative_to(STAGE)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--pattern",action="append",help="Explicit affected suite glob; omit for the full regression.")
    args=parser.parse_args()
    before=hashes();started=time.perf_counter()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    progress_path=args.output.with_suffix('.progress.json')
    class DurableResult(unittest.TextTestResult):
        """Persist completed case outcomes so an interrupted run is auditable."""
        def __init__(self,*values,**kwargs):
            super().__init__(*values,**kwargs);self.completed={}
        def addSuccess(self,test):
            super().addSuccess(test);self.completed[test.id()]={'status':'passed'}
        def addFailure(self,test,error):
            super().addFailure(test,error);self.completed[test.id()]={'status':'failed','traceback':self.failures[-1][1]}
        def addError(self,test,error):
            super().addError(test,error);self.completed[test.id()]={'status':'error','traceback':self.errors[-1][1]}
        def addSkip(self,test,reason):
            super().addSkip(test,reason);self.completed[test.id()]={'status':'skipped','reason':reason}
        def stopTest(self,test):
            super().stopTest(test)
            temporary=progress_path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'complete':False,'completed':self.completed,'source_hashes':before,
                'seconds':time.perf_counter()-started},indent=2),encoding='utf-8')
            temporary.replace(progress_path)
    # Long storage stress cases can otherwise look hung under slow filesystem
    # flushes. Stack snapshots diagnose progress without changing test deadlines.
    faulthandler.dump_traceback_later(120,repeat=True)
    patterns=args.pattern or ['check_*.py']
    suite=unittest.TestSuite(unittest.defaultTestLoader.discover(str(STAGE/"tests"),pattern=pattern) for pattern in patterns)
    result=unittest.TextTestRunner(verbosity=2,resultclass=DurableResult).run(suite)
    faulthandler.cancel_dump_traceback_later()
    unchanged=before==hashes()
    report={"tests":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),
        "skipped":[{"test":str(t),"reason":reason} for t,reason in result.skipped],
        "seconds":round(time.perf_counter()-started,3),"successful":result.wasSuccessful(),
        "sources_unchanged":unchanged,"source_hashes":before,"python":sys.version,"platform":platform.platform(),
        "failure_details":[{"test":str(t),"traceback":trace} for t,trace in result.failures+result.errors],
        "completed":result.completed,"patterns":patterns,"scope":"affected" if args.pattern else "full"}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() and unchanged else 1)


if __name__=="__main__":main()
