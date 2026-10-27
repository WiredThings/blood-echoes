"""Author-only receipt controls; fixtures are not native build acceptance."""
from pathlib import Path
import hashlib,json,os,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import package_native_from_source as package
from build_native_source import source_snapshot

class Manifest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name).resolve();self.repo=self.base/'source';self.root=self.base/'build'
        self.native=self.repo/'experiments/arm64-runtime';self.native.mkdir(parents=True)
        (self.repo/'tools').mkdir();(self.repo/'tools/build.py').write_text('author source\n')
        (self.repo/'SOURCE-MANIFEST.json').write_text(json.dumps({'source_commit':'a'*40}))
        self.prefix=self.base/'deps';(self.prefix/'lib').mkdir(parents=True)
        self.gui=self.root/'gui/Blood Echoes.app';(self.gui/'Contents/MacOS').mkdir(parents=True)
        (self.gui/'Contents/MacOS/bbport').write_bytes(b'author GUI, not native')
        self.layout=self.root/'evidence/layout/result.json';self.layout.parent.mkdir(parents=True);self.layout.write_text('{}')
        paths={'library':'renderer/libbbgpu.dylib','gpu':'renderer/bb-gpu','worker':'renderer/game-pso-preparer',
               'module':'renderer/game-module-preparer','metadata':'renderer/metadata-layout',
               'cpu':'cpu/Bin/bb-game-arm64','net':'net/libbbnet.dylib'}
        artifacts={}
        for key,name in paths.items():
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'author '+key.encode())
            artifacts[key]={'path':str(path),'sha256':package.sha(path)}
        snapshot=source_snapshot(self.repo)
        identity=dict(mode='source-export',revision='a'*40,source_files=snapshot,
            snapshot_sha256=hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(',',':')).encode()).hexdigest())
        self.m=dict(schema=1,status='source-built-not-packaged',repository=str(self.repo),source_root=str(self.native),
            build_root=str(self.root),revision='a'*40,version='0.2.51',targets_built=True,installed=False,game_started=False,
            source_identity=identity,artifacts=artifacts,renderer=str(self.root/'renderer'),cpu=artifacts['cpu']['path'],
            net=artifacts['net']['path'],library=artifacts['library']['path'],layout_result=str(self.layout),
            layout_result_sha256=package.sha(self.layout),gui_app=str(self.gui),
            gui_sha256=package.sha(self.gui/'Contents/MacOS/bbport'),python_provider=str(self.prefix),dependency_prefixes=[str(self.prefix)])
        self.receipt=self.root/'build-manifest.json';self.save()
    def save(self):self.receipt.write_text(json.dumps(self.m))
    def arguments(self,**kw):return package.build_manifest_arguments(self.receipt,output=self.base/'Fresh.app',**kw)
    def reject(self,pattern):
        self.save()
        with self.assertRaisesRegex(ValueError,pattern):self.arguments()
        self.assertFalse((self.base/'Fresh.app').exists())
    def test_actual_receipt_maps_cpu_directory_not_binary_and_explicit_providers(self):
        with patch('subprocess.Popen',side_effect=AssertionError('no native queries for export receipt admission')):
            args=self.arguments(search_dirs=[self.base])
        self.assertEqual(args['cpu_build'],self.root/'cpu')
        self.assertEqual(args['net_library'],self.root/'net/libbbnet.dylib')
        self.assertEqual(args['repository'],self.repo)
        self.assertEqual(args['search_dirs'],[self.prefix,self.prefix/'lib',self.base])
        self.assertFalse((self.base/'Fresh.app').exists())
    def test_unsuccessful_or_used_build_refused(self):
        for key,value in [('status','incomplete'),('targets_built',False),('targets_built',1),('installed',True),('game_started',True)]:
            old=self.m[key];self.m[key]=value;self.reject('successful');self.m[key]=old
    def test_missing_required_field_is_not_a_partial_receipt(self):
        self.m.pop('gui_sha256');self.reject('incomplete')
    def test_duplicate_json_key_refused(self):
        self.receipt.write_text('{"schema":1,"schema":1}')
        with self.assertRaisesRegex(ValueError,'duplicate'):self.arguments()
    def test_receipt_cannot_be_moved_away_from_actual_build_root(self):
        other=self.base/'moved.json';other.write_bytes(self.receipt.read_bytes())
        with self.assertRaisesRegex(ValueError,'location mismatch'):
            package.build_manifest_arguments(other,output=self.base/'Fresh.app')
    def test_receipt_symlink_refused(self):
        alias=self.base/'alias.json';alias.symlink_to(self.receipt)
        with self.assertRaisesRegex(ValueError,'canonical'):
            package.build_manifest_arguments(alias,output=self.base/'Fresh.app')
    def test_current_source_edit_refused(self):
        (self.repo/'tools/build.py').write_text('changed after build')
        with self.assertRaisesRegex(ValueError,'source changed'):self.arguments()
    def test_export_cannot_become_a_git_checkout_after_build(self):
        (self.repo/'.git').mkdir()
        with self.assertRaisesRegex(ValueError,'became a Git'):self.arguments()
    def test_unknown_source_identity_refused(self):
        self.m['source_identity']['mode']='unknown';self.reject('unknown normal')
    def test_inconsistent_revision_refused(self):
        self.m['revision']='b'*40;self.reject('revision mismatch')
    def test_wrong_source_root_refused(self):
        self.m['source_root']=str(self.repo);self.reject('source-root mismatch')
    def test_changed_artifacts_all_seven_roles_refused(self):
        for key,row in self.m['artifacts'].items():
            path=Path(row['path']);old=path.read_bytes();path.write_bytes(old+b'changed')
            with self.assertRaisesRegex(ValueError,'artifact changed: '+key):self.arguments()
            path.write_bytes(old)
    def test_unknown_artifact_role_refused(self):
        self.m['artifacts']['other']={};self.reject('roles mismatch')
    def test_artifact_redirect_even_matching_bytes_refused(self):
        row=self.m['artifacts']['cpu'];other=self.base/'same-cpu';other.write_bytes(Path(row['path']).read_bytes())
        row['path']=str(other);self.reject('artifact changed: cpu')
    def test_alias_does_not_override_actual_roles(self):
        self.m['cpu']=self.m['net'];self.reject('aliases mismatch')
    def test_changed_gui_refused(self):
        (self.gui/'Contents/MacOS/bbport').write_bytes(b'changed GUI')
        with self.assertRaisesRegex(ValueError,'GUI product changed'):self.arguments()
    def test_changed_layout_receipt_refused(self):
        self.layout.write_text('{"changed":true}')
        with self.assertRaisesRegex(ValueError,'layout receipt changed'):self.arguments()
    def test_no_implicit_provider_fallback(self):
        self.m['dependency_prefixes']=[];self.reject('providers missing')
    def test_source_change_during_assembly_prevents_success(self):
        def assemble(**args):
            (self.repo/'tools/build.py').write_text('changed while packaging')
            return {'status':'author assembled'}
        argv=['package','--build-manifest',str(self.receipt),'--output',str(self.base/'Fresh.app')]
        with patch.object(sys,'argv',argv),patch.object(package,'assemble',side_effect=assemble):
            with self.assertRaisesRegex(ValueError,'source changed'):package.main()
    def test_explicit_source_cannot_override_receipt_identity(self):
        argv=['package','--build-manifest',str(self.receipt),'--repository',str(self.repo),'--output',str(self.base/'Fresh.app')]
        with patch.object(sys,'argv',argv),patch.object(package,'build_manifest_arguments',side_effect=AssertionError('must reject before reading receipt')):
            with self.assertRaises(SystemExit) as raised,patch('sys.stderr'):
                package.main()
            self.assertEqual(raised.exception.code,2)
    def test_old_system_python_fails_before_receipt_or_source_reads(self):
        argv=['package','--build-manifest',str(self.receipt),'--output',str(self.base/'Fresh.app')]
        with patch.object(sys,'argv',argv),patch.object(sys,'version_info',(3,9,6)),patch.object(package,'build_manifest_arguments',side_effect=AssertionError('old Python must stop before admission')):
            with self.assertRaises(SystemExit) as raised,patch('sys.stderr') as errors:
                package.main()
            self.assertEqual(raised.exception.code,2)
            self.assertIn('BB_PACKAGE_PYTHON',''.join(str(c) for c in errors.write.call_args_list))

if __name__=='__main__':unittest.main()
