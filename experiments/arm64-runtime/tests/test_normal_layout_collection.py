"""Short sealing/refusal controls using actual authored Writer fixtures.

The fake runner exercises validation only: it never executes its placeholder
binary. Actual native collection is a separate --renderer-build invocation.
"""
from pathlib import Path
import argparse,copy,json,shutil,sys,tempfile,time,unittest
from unittest.mock import patch
D=Path(__file__).resolve().parents[1];sys.path.insert(0,str(D/'tools'))
import build_metadata_layout as tool
P=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
BASE=None
class Runner:
 def __init__(self,test,problem):self.test=test;self.problem=problem
 def run(self,argv,*args,**kwargs):
  t=self.test
  if argv[0]=='/usr/bin/lipo':return b'x86_64\n'if self.problem=='architecture'else b'arm64\n'
  if argv[0]=='/usr/bin/otool':return (str(t.binary)+':\n\t'+('/opt/unwanted/lib.dylib'if self.problem=='edge'else'/usr/lib/libSystem.B.dylib')+' (compatibility version 1.0.0)\n').encode()
  if len(argv)==1:
   data=copy.deepcopy(t.raw)
   if self.problem=='field':del data['Profile.'+t.fields[0][1]]
   return json.dumps(data).encode()
  mode=argv[-1];path=Path(argv[-2]);reference=t.base['authored_writer_fixtures'][mode]
  path.write_bytes(Path(reference['path']).read_bytes());result={k:v for k,v in reference.items()if k not in('path','sha256','canonical_decode_checked')}
  if self.problem=='envelope'and mode=='fetch':result['bytes']+=1
  if mode=='profile-padding':
   if self.problem=='binary':t.binary.write_bytes(t.binary.read_bytes()+b'changed')
   if self.problem=='source':(t.source/tool.HEADERS[0]).write_bytes((t.source/tool.HEADERS[0]).read_bytes()+b'\n// changed\n')
  return json.dumps(result).encode()
class Sealing(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.evidence=P/'evidence'/('normal-layout-controls-'+str(time.time_ns()));cls.evidence.mkdir(mode=0o700);print('evidence:',cls.evidence)
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=self.evidence);self.root=Path(self.temp.name);self.source=self.root/'source';self.source.mkdir();self.out=self.root/'out';self.out.mkdir()
  self.base=json.loads(BASE.read_text())
  for name in tool.HEADERS:
   path=self.source/name;path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(D/'vendor/metal_api/shadps4'/name,path)
  self.emitter=self.root/'metadata_layout.cpp';shutil.copy2(D/'tests/metadata_layout.cpp',self.emitter)
  self.binary=self.root/'placeholder';self.binary.write_text('authored fingerprint fixture, never executed')
  self.fields=tool.profile_fields(self.source);self.headers={name:tool.sha(self.source/name)for name in tool.HEADERS}
  self.raw={k:v for k,v in self.base.items()if k not in('source_sha256','binary_sha256','profile_fields','renderer_loaded','cached_code_registered','authored_writer_fixtures')}
 def tearDown(self):self.temp.cleanup()
 def seal(self,problem=''):
  return tool.seal_layout(source=self.source,out=self.out,binary=self.binary,runner=Runner(self,problem),guard=object(),before_headers=self.headers,emitter=self.emitter,emitter_sha=tool.sha(self.emitter),fields=self.fields)
 def test_complete_seal(self):
  result=self.seal();self.assertTrue(result['strict_canonical_layout_passed']);layout=json.loads(Path(result['layout']).read_text());self.assertEqual(len(layout['authored_writer_fixtures']),7);self.assertFalse(layout['renderer_loaded']);self.assertTrue(all(f['canonical_decode_checked']for f in layout['authored_writer_fixtures'].values()))
 def test_wrong_architecture(self):
  with self.assertRaisesRegex(ValueError,'arm64'):self.seal('architecture')
 def test_third_party_load_edge(self):
  with self.assertRaisesRegex(ValueError,'third-party'):self.seal('edge')
 def test_missing_profile_member(self):
  with self.assertRaisesRegex(ValueError,'profile fields'):self.seal('field')
 def test_fixture_wrong_envelope(self):
  with self.assertRaisesRegex(ValueError,'fixture envelope'):self.seal('envelope')
 def test_binary_changed_mid_collection(self):
  with self.assertRaisesRegex(ValueError,'binary changed'):self.seal('binary')
 def test_source_changed_mid_collection(self):
  with self.assertRaisesRegex(ValueError,'sources changed'):self.seal('source')
 def test_final_strict_loader_not_bypassed(self):
  original=tool.write
  def corrupt(path,value):
   if path.name=='layout.json':value=copy.deepcopy(value);value['source_sha256'][tool.HEADERS[0]]='0'*64
   return original(path,value)
  with patch.object(tool,'write',side_effect=corrupt):
   with self.assertRaisesRegex(ValueError,'source changed'):self.seal()
 def contract_fixture(self,artifact='metadata-layout',arch='arm64'):
  build=self.root/'build';build.mkdir();reply=build/'.cmake/api/v1/reply';reply.mkdir(parents=True)
  (build/'CMakeCache.txt').write_text('CMAKE_HOME_DIRECTORY:PATH='+str(D/'vendor/metal_api')+'\nBB_GAME_PREPARATION_SOURCE_ROOT:PATH='+str(D)+'\nCMAKE_OSX_ARCHITECTURES:STRING='+arch+'\nBB_BUILD_GAME_PREPARATION:BOOL=ON\n')
  (reply/'index-control.json').write_text(json.dumps({'reply':{'codemodel-v2':{'jsonFile':'model.json'}}}))
  (reply/'model.json').write_text(json.dumps({'configurations':[{'targets':[{'name':'metadata-layout','jsonFile':'target.json'}]}]}))
  (reply/'target.json').write_text(json.dumps({'name':'metadata-layout','type':'EXECUTABLE','artifacts':[{'path':artifact}]}));return build
 def test_cmake_wrong_architecture(self):
  with self.assertRaisesRegex(ValueError,'native preparation'):tool.normal_contract(self.contract_fixture(arch='x86_64'),D/'vendor/metal_api/shadps4')
 def test_cmake_artifact_escape(self):
  with self.assertRaisesRegex(ValueError,'escaped'):tool.normal_contract(self.contract_fixture(artifact='../metadata-layout'),D/'vendor/metal_api/shadps4')
 def test_cmake_linked_artifact(self):
  build=self.contract_fixture();(build/'metadata-layout').symlink_to(self.binary)
  with self.assertRaisesRegex(ValueError,'nonlinked'):tool.normal_contract(build,D/'vendor/metal_api/shadps4')
 def test_load_edges_reject_relative(self):
  with self.assertRaisesRegex(ValueError,'third-party'):tool.system_edges('binary:\n\t@rpath/lib.dylib (compatibility version 1)\n')
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--layout',type=Path,required=True);args=parser.parse_args();BASE=args.layout.resolve(strict=True)
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Sealing))
 (Sealing.evidence/'result.json').write_text(json.dumps(dict(tests=result.testsRun,passed=result.wasSuccessful(),author_runner=True,placeholder_binary_executed=False,game_started=False),indent=2)+'\n')
 raise SystemExit(0 if result.wasSuccessful()else 1)
