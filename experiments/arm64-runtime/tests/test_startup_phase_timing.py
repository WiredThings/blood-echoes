"""Only CPU authored callables: startup logs do not execute a native preparer."""
from pathlib import Path
from contextlib import redirect_stdout
import io,json,sys,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import launcher_local_aot as local
import launcher_game_pso as game
import test_launcher_local_aot as local_fixture
import test_launcher_game_pso_automatic as game_fixture

PREFIX='Metal preparation phase: '
def events(text):
 return [json.loads(line[len(PREFIX):])for line in text.splitlines()if line.startswith(PREFIX)]

class Timing(unittest.TestCase):
 def fixture(self,kind):
  value=kind();value.setUp();self.addCleanup(value.tearDown);return value
 def test_monotonic_success_has_only_two_safe_lines(self):
  out=io.StringIO()
  with patch.object(local.time,'monotonic',side_effect=[100.0,101.25]),redirect_stdout(out):
   with local.preparation_phase('local-libraries'):pass
  self.assertEqual(events(out.getvalue()),[dict(phase='local-libraries',event='start'),dict(phase='local-libraries',event='end',seconds=1.25,outcome='returned')])
 def test_unknown_phase_refused_without_logging(self):
  out=io.StringIO()
  with redirect_stdout(out),self.assertRaises(ValueError):
   with local.preparation_phase('/private/secret-token'):pass
  self.assertEqual(out.getvalue(),'')
 def test_exception_and_interrupt_preserve_identity_without_message_leak(self):
  for original in (RuntimeError('/private/secret-token'),KeyboardInterrupt('credential')):
   out=io.StringIO();caught=None
   try:
    with redirect_stdout(out):
     with local.preparation_phase('module-inputs'):raise original
   except BaseException as error:caught=error
   self.assertIs(caught,original)
   lines=events(out.getvalue());self.assertEqual(len(lines),2)
   self.assertEqual(lines[-1]['outcome'],'raised')
   self.assertNotIn('secret',out.getvalue());self.assertNotIn('credential',out.getvalue())
 def test_unwritable_log_cannot_replace_original_return_or_exception(self):
  with patch('builtins.print',side_effect=BrokenPipeError('closed')):
   with local.preparation_phase('fixed-helpers'):value=123
   self.assertEqual(value,123)
   original=RuntimeError('original');caught=None
   try:
    with local.preparation_phase('fixed-helpers'):raise original
   except RuntimeError as error:caught=error
   self.assertIs(caught,original)
 def test_local_boundaries_preserve_calls_results_arguments_and_credentials(self):
  f=self.fixture(local_fixture.Startup);calls=[];before_env=f.env.copy();before_args=f.args[:]
  def archive(**kw):calls.append('archive');return f.identity(**kw)
  def libraries(**kw):calls.append('local');return {},dict(status='prepared')
  def helpers(**kw):calls.append('helpers');return {},dict(status='completed')
  def psos(**kw):calls.append('game');return dict(status='completed',preparation={})
  out=io.StringIO()
  with redirect_stdout(out):
   environment,report=local.prepare_launch(str(f.inputs/'runtime'),f.args,f.env,project=f.root,
    archive_prepare=archive,local_prepare=libraries,fixed_helper_prepare=helpers,game_prepare=psos,
    observe=lambda:dict(locked=False,pressure=1,swap_mib=100))
  self.assertEqual(calls,['archive','local','helpers','game','local'])
  self.assertEqual(report['status'],'prepared');self.assertEqual(environment['BB_THD_TOKEN'],'authored-secret')
  self.assertEqual(f.env,before_env);self.assertEqual(f.args,before_args)
  self.assertEqual([e['phase']for e in events(out.getvalue())if e['event']=='start'],['archive-policy','local-libraries','fixed-helpers','game-preparation','local-finalize'])
  self.assertNotIn(str(f.root),out.getvalue());self.assertNotIn('authored-secret',out.getvalue())
 def test_full_game_phase_order_and_original_runner_result(self):
  f=self.fixture(game_fixture.Entry)
  f.component.update(worker=f.stage/'worker',worker_sha256='d'*64,layout=f.stage/'layout',layout_sha256='e'*64,automatic_capture_schema=1)
  def candidates(**kw):
   result=f.candidates(**kw);result['eligible']=1;return result
  def runner(config):f.calls.append(('runner',config));return dict(status='completed',authored_marker=17)
  def capture(**kw):f.calls.append(('capture',kw));return dict(status='configured',capture_environment={})
  out=io.StringIO()
  with redirect_stdout(out):result=f.prepare(candidate_builder=candidates,runner=runner,capture_configure=capture)
  self.assertEqual([n for n,_ in f.calls],['source-view','catalogue','capture','candidates','modules','runner'])
  self.assertEqual(result['preparation'],dict(status='completed',authored_marker=17))
  self.assertEqual([e['phase']for e in events(out.getvalue())if e['event']=='start'],['source-view','static-catalog','capture-receipt','candidate-scan','module-inputs','complete-pso'])
  self.assertNotIn(str(f.root),out.getvalue());self.assertNotIn('BB_',out.getvalue())
 def test_source_exception_keeps_original_fallback_and_stops_later_phases(self):
  f=self.fixture(game_fixture.Entry)
  from launcher_source_view import SnapshotUnavailable
  def unavailable(**kw):raise SnapshotUnavailable(95,'private-path-should-not-log')
  out=io.StringIO()
  with redirect_stdout(out):result=f.prepare(source_view_prepare=unavailable)
  self.assertEqual(result['status'],'source-fallback');self.assertEqual(f.calls,[])
  seen=events(out.getvalue());self.assertEqual(len(seen),2);self.assertEqual(seen[-1]['outcome'],'raised')
  self.assertNotIn('private-path',out.getvalue())
 def test_no_game_or_explicit_off_does_not_invent_phase_work(self):
  f=self.fixture(game_fixture.Entry);out=io.StringIO()
  with redirect_stdout(out):
   result=f.prepare(execution_root=None)
   env,off=local.prepare_launch('/not-existing',[],{'BB_MTL_LOCAL_AOT_MODE':'off'},project=f.project)
  self.assertEqual(result['status'],'unavailable');self.assertEqual(off['status'],'off');self.assertEqual(f.calls,[])
  self.assertEqual(events(out.getvalue()),[])
 def test_game_module_error_keeps_same_exception_and_no_complete_pso(self):
  f=self.fixture(game_fixture.Entry);original=RuntimeError('/private/account-token')
  def fail(*args,**kw):raise original
  out=io.StringIO();caught=None
  try:
   with redirect_stdout(out):f.prepare(module_prepare=fail)
  except RuntimeError as error:caught=error
  self.assertIs(caught,original)
  seen=events(out.getvalue());self.assertEqual(seen[-1]['phase'],'module-inputs');self.assertEqual(seen[-1]['outcome'],'raised')
  self.assertNotIn('complete-pso',[e['phase']for e in seen]);self.assertNotIn('account-token',out.getvalue())

if __name__=='__main__':unittest.main(verbosity=2)
