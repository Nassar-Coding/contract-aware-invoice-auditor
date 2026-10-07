"""Run actual implementation tests and retain their output/status."""
import hashlib
import io
import json
from pathlib import Path
import platform
import sys
import time
import unittest
from datetime import datetime,timezone

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'src'))
name=sys.argv[1] if len(sys.argv)>1 else 'current'
started=time.perf_counter();log=io.StringIO()
suite=unittest.defaultTestLoader.discover(str(root/'tests'))
result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
files=sorted(list((root/'src').rglob('*.py'))+list((root/'tests').rglob('*.py')))
report={'utc':datetime.now(timezone.utc).isoformat(),'python':platform.python_version(),
        'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
        'success':result.wasSuccessful(),'elapsed_seconds':round(time.perf_counter()-started,6),
        'tested_files':{str(f.relative_to(root)):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}}
(root/'reports').mkdir(exist_ok=True)
(root/f'reports/tests_{name}.txt').write_text(log.getvalue())
(root/f'reports/tests_{name}.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
print(log.getvalue());print(json.dumps({k:v for k,v in report.items() if k!='tested_files'}))
sys.exit(0 if result.wasSuccessful() else 1)
