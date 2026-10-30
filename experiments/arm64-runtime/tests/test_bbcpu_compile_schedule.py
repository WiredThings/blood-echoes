from pathlib import Path
import contextlib,io,json,os,sys,tempfile,threading,time,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from bbcpu_compile_schedule import capacity,run,maximum_jobs,CPU_LIMIT
from bbcpu_launch import stream_cpu_command

class BudgetTests(unittest.TestCase):
    def test_manual_ceiling_tracks_logical_cpu_count_and_auto_remains_conservative(self):
        self.assertEqual(maximum_jobs('auto',cpu_count=10),2)
        self.assertEqual(maximum_jobs('auto',cpu_count=1),1)
        self.assertEqual(maximum_jobs('10',cpu_count=10),10)
        for bad in('0','11','-1','01','+1','2.0','１２',' 2'):
            with self.subTest(value=bad),self.assertRaises(ValueError):maximum_jobs(bad,cpu_count=10)
        self.assertEqual(capacity(dict(pressure=1,headroom_mib=100000),CPU_LIMIT),CPU_LIMIT)
        self.assertEqual(capacity(dict(pressure=2,headroom_mib=100000),CPU_LIMIT),1)
        with self.assertRaises(ValueError):capacity(dict(pressure=1,headroom_mib=100000),CPU_LIMIT+1)
    def test_memory_and_pressure_boundaries(self):
        self.assertEqual(capacity(dict(pressure=1,headroom_mib=8192)),2)
        self.assertEqual(capacity(dict(pressure=2,headroom_mib=8192)),1)
        self.assertEqual(capacity(dict(pressure=4,headroom_mib=8192)),0)
        self.assertEqual(capacity(dict(pressure=1,headroom_mib=512)),0)
        # Measured heavy-unit peak <475 MiB per process; allow 768 MiB per
        # worker plus a separate reserve, without forcing serial at 2.7 GiB.
        self.assertEqual(capacity(dict(pressure=1,headroom_mib=2750)),2)
        self.assertEqual(capacity(dict(pressure=1,headroom_mib=2303)),1)
        self.assertEqual(capacity(dict(pressure=1,headroom_mib=1535)),0)
        with self.assertRaises(ValueError):capacity(dict(pressure=3,headroom_mib=8192))
    def test_pressure_stops_new_dispatch_but_finishes_active_work(self):
        observations=[];calls=0;lock=threading.Lock();active=peak=0
        def observe():
            nonlocal calls
            calls+=1
            return dict(pressure=4 if 2<=calls<=6 else 1,headroom_mib=8192)
        def work(index):
            nonlocal active,peak
            with lock:active+=1;peak=max(peak,active)
            time.sleep(.03)
            with lock:active-=1
            return index*2
        output=run(range(6),work,observe=observe,poll_seconds=.005,progress=lambda **row:observations.append(row))
        self.assertEqual(output,list(range(0,12,2)));self.assertLessEqual(peak,2)
        self.assertTrue(any(row.get('pressure')==4 and row['active']>0 and row['capacity']==0 for row in observations))
        self.assertEqual(observations[-1]['done'],6)
    def test_completed_outputs_survive_later_failure(self):
        completed=[]
        def work(index):
            if index==2:raise RuntimeError('authored compile failure')
            completed.append(index);return index
        with self.assertRaisesRegex(RuntimeError,'authored compile failure'):
            run(range(4),work,maximum=1,observe=lambda:dict(pressure=1,headroom_mib=8192))
        self.assertEqual(completed,[0,1])
    def test_real_child_progress_arrives_before_child_finishes(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp')as temp:
            root=Path(temp);gate=root/'gate';log=root/'child.log'
            code="import json,time,pathlib; print(json.dumps(dict(event='compile-progress',done=0,total=2,active=2,capacity=2)),flush=True); p=pathlib.Path("+repr(str(gate))+"); deadline=time.monotonic()+4\nwhile not p.exists() and time.monotonic()<deadline:time.sleep(.01)\nassert p.exists(),'progress was buffered until exit'\nprint('{\"event\":\"done\"}',flush=True)"
            class Sink(io.StringIO):
                def write(self,value):
                    if value.startswith('CPU preparation progress:'):gate.write_text('observed while child active')
                    return super().write(value)
            with contextlib.redirect_stdout(Sink())as output:
                result=stream_cpu_command([sys.executable,'-B','-c',code],environment=os.environ.copy(),log=log,timeout=6)
            self.assertEqual(result.returncode,0);self.assertTrue(gate.exists())
            self.assertIn('CPU preparation progress:',output.getvalue());self.assertIn('"done"',log.read_text())

if __name__=='__main__':unittest.main()
