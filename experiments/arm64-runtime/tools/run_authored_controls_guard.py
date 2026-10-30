"""Bounded private native-test supervisor; no game launch or visual polling."""
from pathlib import Path
import argparse, json, os, signal, subprocess, sys, time

P = Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy
from qa_build_guard import require_no_games

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--seconds',type=int,default=300)
parser.add_argument('prefix');parser.add_argument('command',nargs=argparse.REMAINDER)
args=parser.parse_args();prefix,command=args.prefix,args.command
assert 1<=args.seconds<=7200 and command
assert prefix.replace('-', '').isalnum()
require_no_games()
observer = NativeObserver()
before = observer.system()
assert before['pressure'] != 4 and not before['locked'], before
evidence = P / 'evidence' / (prefix + '-' + str(time.time_ns()))
evidence.mkdir()
start = time.monotonic()
policy = MemoryPolicy(before)
samples, failure = [], None
with (evidence / 'console.log').open('w') as log:
    process = subprocess.Popen(command, cwd=P, stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True)
    try:
        while process.poll() is None:
            elapsed = time.monotonic() - start
            sample = observer.system()
            samples.append(dict(seconds=elapsed, **sample))
            failure = policy.observe(elapsed, sample)
            if failure or elapsed > args.seconds:
                raise RuntimeError(failure or 'bounded native controls timeout')
            time.sleep(.25)
    except BaseException as error:
        failure = str(error)
        raise
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        result = dict(command=command, exit=process.returncode, reason=failure,
                      before=before, after=observer.system(),
                      seconds=time.monotonic()-start, samples=samples,
                      game_launched=False, time_budget_seconds=args.seconds,memory_policy=MemoryPolicy.MANIFEST)
        (evidence / 'result.json').write_text(json.dumps(result, indent=2)+'\n')
        print(evidence, flush=True)
        print(json.dumps({k:v for k,v in result.items()
                          if k not in ('samples','command')}, indent=2), flush=True)
        if process.returncode == 0:
            print((evidence / 'console.log').read_text()[-2500:])
        else:
            print('Failed: inspect console.log and per-case structured results.')
assert process.returncode == 0
