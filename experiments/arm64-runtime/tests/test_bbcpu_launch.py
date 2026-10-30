"""Scheduling/admission boundaries. Mock tools here are not CPU execution proof."""
from pathlib import Path
import json,os,plistlib,subprocess,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import bbcpu_launch as launch

class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='bbcpu-dispatch-',dir='/private/tmp');self.root=Path(self.temp.name)
        self.stage=self.root/'QA.app/Contents/Resources/bbport';self.stage.mkdir(parents=True,mode=0o700)
        self.project=self.root/'project';self.data=self.project/'state/case';self.out=self.data/'out';self.out.mkdir(parents=True,mode=0o700)
        self.kit=self.root/'producer';self.kit.mkdir(mode=0o700);(self.kit/'produce.py').write_text('# owned source fixture\n')
        for name in('boot-linked.bin','eboot.elf','patches.bin'):(self.out/name).write_bytes(b'authored fixture')
        self.doc=dict(schema=1,development_only=True,backend='bbcpu',producer_root=str(self.kit),producer_entry='produce.py',producer_files={'produce.py':launch.sha(self.kit/'produce.py')},toolchain=dict(compiler='/explicit/clang',linker='/explicit/ld',sdk='/explicit/sdk',dependency_roots=[str(self.kit)]))
        for name,file in dict(cpu='bb-game-bbcpu',preparer='bbcpu-code-preparer',scanner='bbcpu-scan',generator='bbrecomp',abi='bbcpu-abi-identity.json').items():
            p=self.stage/file;p.write_bytes(b'admitted role fixture '+file.encode());self.doc[name]=dict(path=file,sha256=launch.sha(p))
        self.write_doc();(self.stage/'NATIVE-METAL.json').write_text(json.dumps(dict(game_runtime='arm64',production=False,cpu_backends=['fex','bbcpu'],frozen_runtime={row['path']:row['sha256']for key,row in self.doc.items()if key in('cpu','preparer','scanner','generator','abi')})))
        self.env=dict(BB_CPU_BACKEND='bbcpu',BB_DATA_DIR=str(self.data),BB_THD_TOKEN='never-pass-to-compiler',BB_RESTART_NONCE='original-supervisor')
        self.args=[str(self.out/'boot-linked.bin'),'--patches',str(self.out/'patches.bin')];self.calls=[]
    def tearDown(self):self.temp.cleanup()
    def write_doc(self):(self.stage/'BBCPU-COMPONENT.json').write_text(json.dumps(self.doc))
    def child(self,argv,**kw):
        self.calls.append(argv);self.assertNotIn('BB_THD_TOKEN',kw['env'])
        if Path(argv[0]).name=='bbcpu-code-preparer':
            out=Path(argv[argv.index('--out')+1]);out.mkdir(mode=0o700)
            for name in('effective.elf','pointers.txt','effective-input.json'):(out/name).write_text('mock output')
            return subprocess.CompletedProcess(argv,0,'','')
        products=Path(argv[argv.index('--work')+1]);artifact=products/'generations/test';artifact.mkdir(parents=True,mode=0o700,exist_ok=True)
        (artifact/'librecomp.dylib').write_bytes(b'mock product, not execution evidence')
        return subprocess.CompletedProcess(argv,0,json.dumps(dict(event='done',artifact=str(artifact),counters=dict(object_compiles=0)))+'\n','')
    def prepare(self):return launch.prepare('original-fex-role',self.args,self.env,stage=self.stage,project=self.project,runner=self.child)
    def test_single_selected_execution_and_preserved_supervisor_environment(self):
        exe,env,report=self.prepare();self.assertEqual(exe,str(self.stage/'bb-game-bbcpu'));self.assertEqual(env['BB_THD_TOKEN'],self.env['BB_THD_TOKEN']);self.assertEqual(env['BB_RESTART_NONCE'],self.env['BB_RESTART_NONCE']);self.assertTrue(report['effective_exported'])
        _,_,warm=self.prepare();self.assertFalse(warm['effective_exported']);self.assertEqual(sum(Path(a[0]).name=='bbcpu-code-preparer'for a in self.calls),1)
        self.assertFalse(any('fex'in str(p).lower()for a in self.calls for p in a))
    def test_real_code_change_only_reexports_affected_inputs(self):
        self.prepare();(self.data/'unrelated-ui.txt').write_text('changed');self.assertFalse(self.prepare()[2]['effective_exported'])
        (self.out/'patches.bin').write_bytes(b'changed code patch');self.assertTrue(self.prepare()[2]['effective_exported'])
    def test_broken_schedule_preserved_and_rebuilt(self):
        self.prepare();index=next((self.data/'bbcpu-preparation/effective').glob('*.json'));index.write_text('{broken')
        self.assertTrue(self.prepare()[2]['effective_exported']);self.assertTrue(any(p.read_text()=='{broken'for p in index.parent.glob('*previous*.json')))
    def test_changed_kit_refused_before_any_child(self):
        (self.kit/'produce.py').write_text('changed')
        with self.assertRaisesRegex(ValueError,'bytes changed'):self.prepare()
        self.assertEqual(self.calls,[])
    def test_formal_application_refused(self):
        (self.stage.parents[2]/'Contents/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier='io.github.bbport.optimized')))
        with self.assertRaisesRegex(ValueError,'development-only'):self.prepare()
    def test_unknown_backend_and_incomplete_capabilities_refused(self):
        self.env['BB_CPU_BACKEND']='unknown'
        with self.assertRaisesRegex(ValueError,'selected CPU'):self.prepare()
        self.env['BB_CPU_BACKEND']='bbcpu';(self.stage/'NATIVE-METAL.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'capabilities'):self.prepare()
    def test_original_exit_error_not_restart(self):
        def failed(*a,**kw):return subprocess.CompletedProcess(a,3,'','authored tool error')
        with self.assertRaisesRegex(ValueError,'effective-code failed'):launch.prepare('original-fex-role',self.args,self.env,stage=self.stage,project=self.project,runner=failed)
        self.assertNotIn('BB_RECOMP_LIB',self.env)
    def test_compile_guard_recognizes_the_host_not_its_offline_tool(self):
        from qa_build_guard import parse_game_processes
        rows='10 /private/tmp/bb-game-bbcpu\n11 /private/tmp/bb-game-bbcpu-check\n12 /private/tmp/bbcpu-scan\n'
        self.assertEqual([pid for pid,_ in parse_game_processes(rows)],[10,11])

if __name__=='__main__':unittest.main()
