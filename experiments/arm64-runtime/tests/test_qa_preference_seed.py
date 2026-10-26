"""QA-only UserDefaults admission; fake runner never writes real preferences."""
from pathlib import Path
import json,plistlib,sys,tempfile,unittest
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from package_local_arm64_qa import seed_qa_preferences

class PreferenceSeed(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.p=Path(self.temp.name).resolve()/'prototype';self.p.mkdir();self.calls=[];self.saved={};self.exists=False;self.corrupt=False
  app=self.p/'apps/Author QA.app';(app/'Contents').mkdir(parents=True)
  root=self.p/'state/author';root.mkdir(parents=True);user=root/'user';user.mkdir();game=root/'game';game.mkdir()
  self.case=dict(bundle_id='io.github.bbport.optimized.options-qa.arm64.gui.12345',app=str(app),root=str(root),user=str(user),preferences=str(root/'preferences.plist'))
  self.values=dict(optimizedFoldersImported=True,online=False,cloudSaves=False,userFolder=str(user),gameFolder=str(game),quicksave=True,extraEnv='BB_AUDIO=none')
  self.write()
 def write(self):
  app=Path(self.case['app']);(app/'Contents/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier=self.case['bundle_id'],BBPreviewDataPath=self.case['root'])))
  Path(self.case['preferences']).write_bytes(plistlib.dumps(self.values))
 def runner(self,args,**kw):
  self.calls.append(args)
  if args[1]=='import':self.saved=plistlib.loads(Path(args[3]).read_bytes());self.exists=True;return SimpleNamespace(returncode=0,stdout=b'')
  if not self.exists:return SimpleNamespace(returncode=1,stdout=b'')
  return SimpleNamespace(returncode=0,stdout=plistlib.dumps({}if self.corrupt else self.saved))
 def seed(self):return seed_qa_preferences(self.case,project=self.p,runner=self.runner)
 def test_fresh_import_is_read_back(self):
  r=self.seed();self.assertTrue(r['imported_into_original_user_defaults']);self.assertEqual(self.saved,self.values);self.assertEqual([x[1]for x in self.calls],['export','import','export'])
 def test_production_bundle_rejected_before_defaults(self):
  self.case['bundle_id']='io.github.bbport.optimized';self.write()
  with self.assertRaises(AssertionError):self.seed()
  self.assertEqual(self.calls,[])
 def test_cloud_or_online_seed_refused(self):
  for key in('online','cloudSaves'):
   self.values[key]=True;self.write()
   with self.assertRaises(AssertionError):self.seed()
   self.values[key]=False
  self.assertEqual(self.calls,[])
 def test_existing_domain_never_overwritten(self):
  self.exists=True;self.saved={'keep':'existing author value'}
  with self.assertRaisesRegex(AssertionError,'existing preferences'):self.seed()
  self.assertEqual([x[1]for x in self.calls],['export'])
 def test_macos_successful_empty_export_is_an_empty_domain(self):
  self.exists=True;self.saved={}
  r=self.seed();self.assertFalse(r['existing_values_overwritten']);self.assertEqual(self.saved,self.values)
 def test_formal_progress_path_refused_before_defaults(self):
  self.values['userFolder']='/';self.case['user']='/';self.write()
  with self.assertRaises(AssertionError):self.seed()
  self.assertEqual(self.calls,[])
 def test_failed_readback_is_not_success(self):
  self.corrupt=True
  with self.assertRaisesRegex(AssertionError,'readback differs'):self.seed()
if __name__=='__main__':unittest.main()
