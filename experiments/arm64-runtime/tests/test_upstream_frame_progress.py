"""Actual CSV progress/summary and sample loop controls; no game/process/Cua."""
from pathlib import Path
from contextlib import ExitStack, redirect_stdout
import csv
import io
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import run_qa_launch_benchmark as driver
from diagnostic_frame_tail import read_tail

FIELDS = ['t_s','frame_ms','draws','gpu_ms','cam_x','cam_y','cam_z']

def row(t, ms=20):
    return dict(t_s=f'{t:.4f}',frame_ms=ms,draws=1000,gpu_ms=16,cam_x=1,cam_y=2,cam_z=3)

def write_csv(path, rows):
    with path.open('w') as output:
        writer=csv.DictWriter(output,fieldnames=FIELDS);writer.writeheader();writer.writerows(rows)

def append_csv(path, rows):
    with path.open('a') as output:csv.DictWriter(output,fieldnames=FIELDS).writerows(rows)

class FrameProgressControls(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='upstream-frame-progress-',dir='/private/tmp')
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.frames=self.root/'frames.csv'
        self.progress=driver.FrameProgress()
    def observe(self, seconds):self.progress.observe(self.frames,seconds,read_tail(self.frames))
    def start(self):
        write_csv(self.frames,[row(10)]);self.observe(5);self.progress.start_scene(5)
    def samples(self,end):return [dict(seconds=s,usage=[dict(footprint_bytes=16*1024*1024)]) for s in range(5,int(end)+1)]
    def summary(self,end=65):
        return driver.scene_summary(self.frames,self.samples(end),5,60,
                                    frame_progress=self.progress.summary(end))
    def test_old_frozen_tail_counterexample_is_rejected(self):
        self.start();append_csv(self.frames,[row(10+i*.02) for i in range(1,101)]);self.observe(7)
        for seconds in range(8,76):self.observe(seconds)
        self.assertEqual(self.progress.completion_reason(75,60),'scene frame progress time budget')
        with self.assertRaisesRegex(ValueError,'stale frame'):self.summary(75)
    def test_no_live_observation_cannot_authorize_an_old_frame_file(self):
        write_csv(self.frames,[row(10+i*.02) for i in range(101)])
        with self.assertRaisesRegex(ValueError,'live frame progress'):driver.scene_summary(self.frames,self.samples(65),5,60)
    def test_continuous_frames_match_whole_wall_window(self):
        self.start()
        for second in range(1,61):
            append_csv(self.frames,[row(10+second-1+i*.02) for i in range(1,51)]);self.observe(5+second)
        self.assertEqual(self.progress.completion_reason(65,60),'scene sample completed')
        result=self.summary()
        self.assertEqual(result['frames'],3000);self.assertAlmostEqual(result['fps'],50)
        self.assertEqual(result['frame_progress']['wall_seconds'],60)
        self.assertEqual(result['frame_progress']['clock_seconds'],60)
    def test_short_native_batch_flush_waits_for_real_post_deadline_advance(self):
        self.start()
        append_csv(self.frames,[row(10+i*.02) for i in range(1,2951)]);self.observe(64)
        self.observe(65)
        self.assertIsNone(self.progress.completion_reason(65,60))
        append_csv(self.frames,[row(10+i*.02) for i in range(2951,3101)]);self.observe(67)
        self.assertEqual(self.progress.completion_reason(67,60),'scene sample completed')
        result=self.summary(67);self.assertAlmostEqual(result['fps'],50)
        self.assertEqual(result['frame_progress']['wall_seconds'],62)
    def test_recent_but_pre_deadline_tail_is_not_complete(self):
        self.start();append_csv(self.frames,[row(10+i*.02) for i in range(1,3001)]);self.observe(64)
        self.observe(65)
        self.assertIsNone(self.progress.completion_reason(65,60))
        with self.assertRaisesRegex(ValueError,'unmatched'):self.summary(65)
    def test_mid_run_long_stall_is_retained_even_after_recovery(self):
        self.start()
        for second in range(1,61):
            if 11<=second<=30:
                self.observe(5+second);continue
            append_csv(self.frames,[row(10+second-1+i*.02)for i in range(1,51)]);self.observe(5+second)
        result=self.summary()
        self.assertAlmostEqual(result['fps'],2000/60)
        self.assertGreaterEqual(result['max_frame_ms'],20000)
        self.assertGreaterEqual(result['frame_progress']['max_observed_no_progress_seconds'],20)
        self.assertEqual(result['frame_progress']['clock_seconds'],60)
    def test_fast_forward_clock_cannot_replace_wall_coverage(self):
        self.start();append_csv(self.frames,[row(100+i*.02)for i in range(1,101)]);self.observe(65)
        with self.assertRaisesRegex(ValueError,'wall time'):self.summary()
    def test_truncated_replaced_or_missing_file_is_rejected(self):
        for kind in ('truncated','replaced','missing'):
            with self.subTest(kind=kind):
                self.progress=driver.FrameProgress();self.start()
                append_csv(self.frames,[row(10+i*.02)for i in range(1,101)]);self.observe(7)
                if kind=='truncated':write_csv(self.frames,[row(0)])
                elif kind=='replaced':
                    other=self.root/'other.csv';write_csv(other,[row(20)]*500);os.replace(other,self.frames)
                else:self.frames.unlink()
                with self.assertRaisesRegex(ValueError,'truncated|replaced|disappeared'):self.progress.observe(self.frames,8,[])
    def test_clock_reset_without_shrinking_file_is_rejected(self):
        self.start();append_csv(self.frames,[row(1)]*100)
        with self.assertRaisesRegex(ValueError,'clock moved backwards'):self.observe(6)
    def test_nonfinite_latest_clock_is_rejected(self):
        self.start();append_csv(self.frames,[row(float('nan'))])
        with self.assertRaisesRegex(ValueError,'invalid latest frame clock'):self.observe(6)
    def test_replaced_file_after_completion_is_not_summary_input(self):
        self.start();append_csv(self.frames,[row(10+i*.02)for i in range(1,3001)]);self.observe(65)
        progress=self.progress.summary(65);other=self.root/'other.csv';write_csv(other,[row(70)]*500);os.replace(other,self.frames)
        with self.assertRaisesRegex(ValueError,'changed after'):driver.scene_summary(self.frames,self.samples(65),5,60,frame_progress=progress)

class FakeClock:
    def __init__(self):self.now=0.
    def monotonic(self):return self.now
    def sleep(self,seconds):
        self.now+=seconds
        if self.now>90:raise AssertionError('bounded fake sample loop exceeded')

class FakeProcess:
    pid=123456789
    def __init__(self):self.returncode=None
    def poll(self):return self.returncode
    def terminate(self):self.returncode=0
    def wait(self,timeout=None):return self.returncode
    def kill(self):self.returncode=-9

class SampleLoopControls(unittest.TestCase):
    def execute(self, mode, *, old_file=False):
        with tempfile.TemporaryDirectory(prefix='upstream-sample-loop-',dir='/private/tmp') as td:
            root=Path(td);data=root/'data';(data/'out').mkdir(parents=True);evidence=root/'evidence';evidence.mkdir()
            frames=data/'out/frames.csv';pad=data/'pad';pad.write_text('')
            if old_file:write_csv(frames,[row(0)])
            clock=FakeClock();process=FakeProcess();state=dict(started=False,last=-1)
            class Observer:
                def system(self):
                    if state['started'] and process.poll()is None and not(mode=='frozen'and clock.now>10):
                        last=int(clock.now*50)
                        if not frames.exists():write_csv(frames,[])
                        append_csv(frames,[row(i/50)for i in range(state['last']+1,last+1)]);state['last']=last
                    return dict(pressure=1,locked=False,swap_mib=0)
                def processes(self,parent):return[(parent,0,'/private/fake/bb-probe-metal')]
                def path(self,pid):return None
            class Policy:
                def observe(self,seconds,sample):return None
            def start(*args,**kwargs):state['started']=True;return process
            case=dict(pad=pad,data_root=data,source_save_manifest={})
            conditions=dict(stage=root,user_root=root/'private-user',runtime={})
            report={}
            with ExitStack()as stack,redirect_stdout(io.StringIO()):
                stack.enter_context(patch.object(driver.time,'monotonic',clock.monotonic))
                stack.enter_context(patch.object(driver.time,'sleep',clock.sleep))
                spawn=stack.enter_context(patch.object(driver.subprocess,'Popen',side_effect=start))
                stack.enter_context(patch.object(driver.os,'kill',side_effect=AssertionError('no real PID cleanup allowed')))
                stack.enter_context(patch.object(driver,'usage',return_value=[dict(role='bb-probe-metal',footprint_bytes=1024*1024)]))
                stack.enter_context(patch('qa_launch_clone.inventory_savedata',return_value={}))
                if old_file:
                    with self.assertRaisesRegex(ValueError,'old frame log'):driver.sample(conditions,case,{},['no-executable'],report,evidence,startup_seconds=60,scene_seconds=45,observer=Observer(),policy=Policy(),protection_start=0)
                    self.assertEqual(spawn.call_count,0);return
                value=driver.sample(conditions,case,{},['no-executable'],report,evidence,startup_seconds=60,scene_seconds=45,observer=Observer(),policy=Policy(),protection_start=0)
            self.assertEqual(spawn.call_count,1)
            return value
    def test_actual_sample_loop_continuous_frames_are_sampled(self):
        result=self.execute('continuous')
        self.assertEqual(result['status'],'sampled',result)
        self.assertAlmostEqual(result['scene_sample']['fps'],50)
    def test_actual_sample_loop_frozen_tail_is_incomplete(self):
        result=self.execute('frozen')
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(result['stop'],'scene frame progress time budget')
        self.assertNotIn('scene_sample',result)
    def test_old_csv_is_rejected_before_process_start(self):self.execute('continuous',old_file=True)

if __name__=='__main__':unittest.main(verbosity=2)
