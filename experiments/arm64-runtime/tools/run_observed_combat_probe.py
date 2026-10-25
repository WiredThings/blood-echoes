"""Private silent route through the unchanged launcher; capture outside audit gates."""
from pathlib import Path
import argparse,subprocess,time,re,json,sys
p=Path(__file__).resolve().parents[1];ap=argparse.ArgumentParser();ap.add_argument('--save-seed',type=Path,required=True);ap.add_argument('--library-reuse',choices=['0','1']);a=ap.parse_args();seed=a.save_seed.absolute()
assert seed==seed.resolve()and seed.is_relative_to(p/'state')and(seed/'savedata').is_dir(),'existing private QA seed required'
tag=str(time.time_ns());driver_e=p/'evidence'/('observed-combat-driver-'+tag);driver_e.mkdir();logpath=driver_e/'console.log'
command=['python3',str(p/'tools/run_launcher_engine_abba.py'),'--order','arm64','--seconds','180','--action-trace','--producer-trace','--producer-waits','--unaligned-audit','--compile-audit','--exploration-diagnostic','--gpu-variant','present-image','--game-images','--external-image-control','--save-seed',str(seed)]
if a.library_reuse is not None:command+=['--library-reuse',a.library_reuse]
recipe=[(0,'move','lx=128 ly=0'),(2.16,'move','lx=128 ly=0'),(4.26,'move','lx=128 ly=0'),(6.37,'move','lx=128 ly=0'),(8.48,'move','lx=0 ly=128'),(11,'capture',''),(28,'gate','on'),(32.02,'move','lx=255 ly=0'),(34.17,'move','lx=128 ly=0'),(40,'gate','off'),(60.76,'move','lx=255 ly=0'),(62.88,'move','lx=255 ly=0'),(100,'gate','on'),(104.07,'move','lx=128 ly=0'),(108,'attack','r1'),(110,'attack','r2'),(112,'gate','off'),(130,'gate','on'),(132,'attack','r1'),(134,'attack','r1'),(136,'attack','r2'),(140,'gate','off'),(142,'capture','')]
case=None;rows=[];failure=None
with logpath.open('w')as log:
 proc=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT);print('LOG',logpath,flush=True)
 try:
  deadline=time.monotonic()+60
  while time.monotonic()<deadline:
   if proc.poll()is not None:raise RuntimeError('supervisor stopped before scene ready')
   m=re.search(r'Evidence: (.+)',logpath.read_text())
   if m:
    case=Path(m[1])/'0-arm64'
    if(case/'external-ready.json').exists():break
   time.sleep(.1)
  else:raise RuntimeError('scene not ready within60sec')
  print('CASE',case,flush=True);a=['python3',str(p/'tools/qa_image_action.py'),str(case)]
  events=[json.loads(x)for x in(case/'operator-events.jsonl').read_text().splitlines()];ready=next(x['mono_ns']for x in events if x['event']=='external_image_control_ready')/1e9
  for target,kind,value in recipe:
   while time.clock_gettime(time.CLOCK_MONOTONIC)-ready<target:
    if proc.poll()is not None:raise RuntimeError('guarded supervisor stopped during route')
    time.sleep(.1)
   elapsed=time.clock_gettime(time.CLOCK_MONOTONIC)-ready
   if elapsed-target>8:raise RuntimeError('route scheduling drift exceeds8sec')
   args=['--gate',value]if kind=='gate'else['--capture']if kind=='capture'else['--input',value,'--hold','2'if kind=='move'else'.4']
   subprocess.run(a+args,check=True,timeout=15);rows.append(dict(target_s=target,actual_s=elapsed,kind=kind,value=value));print('step',kind,target,flush=True)
 except BaseException as exc:failure=str(exc);print('DRIVER FAILURE',failure,flush=True)
 finally:
  if case is not None:
   (case/'route-requests.json').write_text(json.dumps(dict(route='observed-Old-Yharnam-lamp-plaza-v1',deterministic_replay=False,death_acceptance=False,performance_evidence=False,steps=rows,failure=failure),indent=2)+'\n')
   if proc.poll()is None and(case/'external-ready.json').exists():subprocess.run(['python3',str(p/'tools/qa_image_action.py'),str(case),'--finish'],check=False,timeout=15)
  # The existing supervisor owns game teardown and its 180s guards; never kill it.
  status=proc.wait(timeout=200)
  print('SUPERVISOR',status,'LOG',logpath,flush=True)
passed=not failure and status==0
result=dict(passed=passed,case=str(case)if case else None,console=str(logpath),command=command,route_requests=rows,failure=failure,supervisor_exit=status,performance_evidence=False,combat_contact_independently_verified=False,qualification='Observed broad route only; verify native images before claiming combat or white-model coverage.')
(driver_e/'result.json').write_text(json.dumps(result,indent=2)+'\n')
if not passed:sys.exit(1)
