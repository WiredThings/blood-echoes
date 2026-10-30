"""Actual child execution: refuse native targets and bad HLE, preserve exit 74."""
import argparse,subprocess
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--probe',type=Path,required=True);a=p.parse_args()
    for mode in ('--raw-host','--unowned-syscall','--bad-pointer','--wrong-operation'):
        result=subprocess.run([str(a.probe.resolve(strict=True)),mode],capture_output=True,text=True,timeout=15)
        assert result.returncode==74,(mode,result.returncode,result.stdout,result.stderr)
        assert 'STOP: bbcpu:'in result.stderr,(mode,result.stderr)
        print(mode,'refused, original HLE fault 74; not restart 75/76')
if __name__=='__main__':main()
