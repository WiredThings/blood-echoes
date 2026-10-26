"""Short authored configuration/generator controls; no renderer/FEX build."""
from pathlib import Path
import ast,json,os,subprocess,sys,tempfile,time,unittest
D=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(D/'tools'))
from configure_native_build import PRIVATE_RUNTIME,plan,renderer_inputs

class Configuration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evidence=PRIVATE_RUNTIME/'evidence'/('native-build-author-controls-'+str(time.time_ns()))
        cls.evidence.mkdir(mode=0o700)
        print('evidence:',cls.evidence,flush=True)
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(dir=self.evidence)
        self.root=Path(self.temporary.name);self.build=self.root/'renderer';self.build.mkdir()
        self.prefix=self.root/'deps';self.prefix.mkdir();self.sdl=self.prefix/'libSDL3.dylib';self.sdl.write_text('authored path fixture, not a binary')
        self.renderer=D/'vendor/metal_api';self.reply=self.build/'.cmake/api/v1/reply';self.reply.mkdir(parents=True)
        self.cache=dict(CMAKE_HOME_DIRECTORY=str(self.renderer),CMAKE_OSX_ARCHITECTURES='arm64',BB_BUILD_GAME_PREPARATION='ON',BB_GAME_PREPARATION_SOURCE_ROOT=str(D))
        self.target=dict(artifacts=[dict(path='libbbgpu.dylib')],link=dict(commandFragments=[dict(fragment=str(self.sdl))]))
        self.write()
    def tearDown(self):self.temporary.cleanup()
    def write(self):
        (self.build/'CMakeCache.txt').write_text(''.join(k+':STRING='+v+'\n'for k,v in self.cache.items()))
        (self.reply/'index-control.json').write_text(json.dumps(dict(reply={'codemodel-v2':dict(jsonFile='model.json')})))
        (self.reply/'model.json').write_text(json.dumps(dict(configurations=[dict(targets=[dict(name='bbgpu',jsonFile='target.json')])])))
        (self.reply/'target.json').write_text(json.dumps(self.target))
        rows=[]
        for name in('shim/bbgpu.cpp','shadps4/core/libraries/avplayer/avplayer_state.cpp','shadps4/core/libraries/avplayer/avplayer_source.cpp'):
            rows.append(dict(file=str(self.renderer/name),directory=str(self.build),arguments=['clang++','-DREAL_CURRENT_FIELD=1','-I'+str(self.renderer/'shim'),'-isystem',str(self.renderer/'shadps4'),'-o','CMakeFiles/bbgpu.dir/'+name+'.o','-c',str(self.renderer/name)]))
        (self.build/'compile_commands.json').write_text(json.dumps(rows))
    def make_plan(self,**options):
        args=dict(source_root=D,build_dir=self.root/'cpu',generated_dir=self.root/'generated',dependency_prefixes=[self.prefix],renderer_build=self.build)
        args.update(options);return plan(**args)
    def test_explicit_plan_no_output_or_historical_replay(self):
        value=self.make_plan();self.assertFalse((self.root/'generated').exists())
        self.assertIn('-DREAL_CURRENT_FIELD=1',value['renderer']['flags'])
        self.assertEqual(value['renderer']['library'],str(self.build/'libbbgpu.dylib'))
        self.assertIn('-DBB_ARM64_GENERATED_DIR='+str(self.root/'generated'),value['configure'])
        self.assertIn('-DBB_ARM64_RUNTIME_ROOT='+str(PRIVATE_RUNTIME),value['configure'])
        self.assertTrue(value['configure_ready']);self.assertFalse(value['cpu_host']['actual_load_and_native_ABI_validated'])
        self.assertFalse(any(a.startswith('-DBB_ARM_GPU_LIBRARY=')for a in value['configure']))
        self.assertNotIn('evidence/configure.json',str(value));self.assertFalse(value['ABI_validated'])
    def test_wrong_source_rejected(self):
        self.cache['CMAKE_HOME_DIRECTORY']=str(self.root);self.write()
        with self.assertRaisesRegex(ValueError,'another source'):self.make_plan()
    def test_wrong_architecture_rejected(self):
        self.cache['CMAKE_OSX_ARCHITECTURES']='x86_64';self.write()
        with self.assertRaisesRegex(ValueError,'exactly arm64'):self.make_plan()
    def test_missing_preparation_rejected(self):
        self.cache['BB_BUILD_GAME_PREPARATION']='OFF';self.write()
        with self.assertRaisesRegex(ValueError,'preparation targets'):self.make_plan()
    def test_artifact_escape_rejected(self):
        self.target['artifacts'][0]['path']='../libbbgpu.dylib';self.write()
        with self.assertRaisesRegex(ValueError,'escaped'):self.make_plan()
    def test_runtime_isolation_not_relocated(self):
        with self.assertRaisesRegex(ValueError,'must not be relocated'):self.make_plan(runtime_root=self.root)
    def test_generated_source_tree_rejected(self):
        with self.assertRaisesRegex(ValueError,'independent'):self.make_plan(generated_dir=D/'generated-new')
    def test_gpu_generator_uses_independent_output(self):
        original=(D/'generated/gpu_adapters.cpp').read_bytes();out=self.root/'gpu-generated'
        result=subprocess.run([sys.executable,str(D/'tools/generate_gpu.py'),'--source-root',str(D),'--output-dir',str(out)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        text=(out/'gpu_adapters.cpp').read_text();self.assertIn(str(self.renderer),text)
        self.assertIn('MakeHleCallAdapter',text);self.assertNotIn('/evidence/',text)
        self.assertEqual((D/'generated/gpu_adapters.cpp').read_bytes(),original)
    def test_gpu_header_escape_rejected(self):
        manifest=self.root/'bad.json';manifest.write_text(json.dumps({'../outside.h':['x']}))
        result=subprocess.run([sys.executable,str(D/'tools/generate_gpu.py'),'--source-root',str(D),'--output-dir',str(self.root/'bad-generated'),'--header-manifest',str(manifest)],capture_output=True,text=True,timeout=10)
        self.assertNotEqual(result.returncode,0);self.assertFalse((self.root/'bad-generated/gpu_adapters.cpp').exists())
    def test_native_generator_preserves_algorithm_with_authored_ast(self):
        source=self.root/'source';runtime=source/'vendor/bb_runtime/src';runtime.mkdir(parents=True);(source/'src').mkdir()
        tree=ast.parse((D/'tools/generate_native.py').read_text())
        names=next(ast.literal_eval(n.value)for n in tree.body if isinstance(n,ast.Assign)and any(isinstance(x,ast.Name)and x.id=='names'for x in n.targets))
        for name in names:(runtime/(name+'.c')).write_text('/* authored AST input fixture */\n')
        payload=dict(inner=[dict(kind='FunctionDecl',name='authored_control',type={'qualType':'int (int)'},inner=[dict(kind='ParmVarDecl',type={'qualType':'int'}),dict(kind='CompoundStmt'),dict(kind='AnnotateAttr',inner=[dict(kind='ConstantExpr',value=str(0x42424755455354))])])])
        compiler=self.root/'authored-ast.py';compiler.write_text('#!/usr/bin/env python3\nprint('+repr(json.dumps(payload))+')\n');compiler.chmod(0o755)
        out=self.root/'native-generated'
        result=subprocess.run([sys.executable,str(D/'tools/generate_native.py'),'--source-root',str(source),'--output-dir',str(out),'--compiler',str(compiler),'--include-dir',str(self.prefix)],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        metadata=json.loads((out/'native-signatures.json').read_text());self.assertEqual(len(metadata),len(names));self.assertTrue(all(m['signature']=='ii'for m in metadata))
        self.assertIn(str(runtime/'runtime_file.c'),(out/'runtime_file_bridge.c').read_text())
        self.assertFalse((source/'generated').exists())

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Configuration))
    (Configuration.evidence/'result.json').write_text(json.dumps(dict(tests=result.testsRun,passed=result.wasSuccessful(),
        authored_controls=True,actual_runtime_ABI_validated=False,renderer_built=False,FEX_built=False,game_started=False),indent=2)+'\n')
    raise SystemExit(0 if result.wasSuccessful()else 1)
