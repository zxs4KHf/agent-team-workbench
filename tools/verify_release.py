"""Reproducible offline release checks; no deployment or production-state writes."""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
import time
ROOT = Path(__file__).resolve().parents[1]


def fingerprint():
    digest=hashlib.sha256()
    paths=list(ROOT.glob('*.py'))+list((ROOT/'web/v2').rglob('*'))+list((ROOT/'tests').glob('*.py'))
    for path in sorted(paths):
        if not path.is_file() or '__pycache__' in path.parts:
            continue
        digest.update(str(path.relative_to(ROOT)).replace(chr(92),'/').encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--suite',choices=['unit','ui','smoke','new','all'],default='all')
    parser.add_argument('--output',default='docs/e2e-20260925/evidence')
    args=parser.parse_args()
    destination=ROOT/args.output; destination.mkdir(parents=True,exist_ok=True)
    commands=[]
    py=[sys.executable,'-X','utf8','-u']
    if args.suite in ('new',):
        for name in ('test_e2e_regressions_20260925.py','test_verified_build_e2e.py','test_project_transactions_e2e.py'):
            commands.append((name,py+['tests/'+name]))
    if args.suite in ('unit','all'):
        commands.append(('unit-discovery',py+['-m','unittest','discover','-s','tests','-p','test_*.py','-v']))
    if args.suite in ('ui','all'):
        commands.append(('browser-integration',py+['-m','unittest','discover','-s','tests','-p','ui_*.py','-v']))
        commands.append(('real-page-probe',py+['tests/probe_page_e2e.py']))
    if args.suite in ('smoke','all'):
        commands.append(('module-parse',['node','--experimental-vm-modules','tests/check_module_parse.cjs']))
        for path in sorted((ROOT/'tests').glob('smoke_*.mjs')):
            commands.append((path.stem,['node',str(path.relative_to(ROOT))]))
    started=datetime.datetime.now().isoformat(timespec='seconds')
    before=fingerprint()
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    results=[]
    for name,argv in commands:
        now=time.monotonic()
        print('RUN',name,flush=True)
        try:
            proc=subprocess.run(argv,cwd=ROOT,capture_output=True,timeout=600,
                 env={**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONUTF8':'1','BUS_WORKSPACE':str(ROOT)})
            text=(proc.stdout+proc.stderr).decode('utf-8','replace')
            status=proc.returncode
        except subprocess.TimeoutExpired as exc:
            text=((exc.stdout or b'')+(exc.stderr or b'')).decode('utf-8','replace')+' TIMEOUT'
            status=124
        except Exception as exc:
            status=125; text=type(exc).__name__+': '+str(exc)
        (destination/(name+'.log')).write_text(text,encoding='utf-8')
        result={'name':name,'exit_code':status,'seconds':round(time.monotonic()-now,3),'log':name+'.log'}
        results.append(result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        print(chr(10).join(text.splitlines()[-5:]),flush=True)
    after=fingerprint()
    record={'started':started,'finished':datetime.datetime.now().isoformat(timespec='seconds'),
            'head':head,'suite':args.suite,'source_before':before,'source_after':after,
            'source_unchanged':before==after,'results':results,
            'ok':before==after and all(result['exit_code']==0 for result in results)}
    (destination/('manifest-'+args.suite+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print('RELEASE_CHECK',json.dumps({'ok':record['ok'],'source_unchanged':before==after,'commands':len(results)}),flush=True)
    return 0 if record['ok'] else 1

if __name__=='__main__':
    raise SystemExit(main())
