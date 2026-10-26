# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual relocated CPU scalar executables + exact freeze_inputs/capability.
GPU-edge/layout interfaces are authored mocks; no GPU bytes/library are loaded.
"""
from pathlib import Path
import argparse,ast,hashlib,json,os,shutil,stat,subprocess,sys,tempfile,types,unittest,select,signal,time
from unittest.mock import patch
p=argparse.ArgumentParser(add_help=False);p.add_argument('--source-root',type=Path,required=True);p.add_argument('--worker-new',type=Path,required=True);p.add_argument('--worker-old',type=Path,required=True);a,left=p.parse_known_args();sys.argv=[sys.argv[0],*left]
source=a.source_root/'tools/game_pso_component.py';tree=ast.parse(source.read_text());names={'need','sha','relative','_CapabilityExitWatch','_signal_capability_group','_run_bounded_capability','fixed_helper_capability','freeze_inputs'};nodes=[n for n in tree.body if isinstance(n,(ast.ClassDef,ast.FunctionDef))and n.name in names];assert len(nodes)==len(names)
ns=dict(Path=Path,os=os,stat=stat,subprocess=subprocess,tempfile=tempfile,shutil=shutil,json=json,hashlib=hashlib,select=select,signal=signal,time=time,
 checked_bytes=lambda p,**kw:(Path(p).read_bytes(),{}),load_canonical_layout=lambda *_:{'source_sha256':{}})
exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),ns)
class Publishing(unittest.TestCase):
 def run_case(self,worker,claimed,expected):
  with tempfile.TemporaryDirectory()as tmp:
   root=Path(tmp).resolve();(root/'state').mkdir();stage=root/'stage';(stage/'bin').mkdir(parents=True);library=root/'input-library';library.write_bytes(b'author metadata identity only, never loaded');dstlib=stage/'author-library';dstlib.write_bytes(library.read_bytes());layout=root/'layout.json';layout.write_text('{}');headers=root/'headers';headers.mkdir();m=dict(schema=1,kind='blood-echoes-game-pso-component',production=False,worker=str(worker.resolve()),worker_sha256=ns['sha'](worker.resolve()),library=str(library),library_sha256=ns['sha'](library),layout=str(layout),source_root=str(headers),layout_sha256=ns['sha'](layout),fixed_helper_seed_schema=claimed);manifest=root/'input.json';manifest.write_text(json.dumps(m));events=[]
   resolve=lambda *_:dict(library=str(library),library_sha256=ns['sha'](library))
   real=ns['fixed_helper_capability']
   def query(target,admission):events.append(('actual-query',str(target)));self.assertEqual(events[0][0],'rewrite');return real(target,admission)
   def rewrite(target,*_):events.append(('rewrite',str(target)));self.assertEqual(target,stage/'bin/game-pso-preparer')
   with patch.dict(sys.modules,{'warm_archive_manifest':types.SimpleNamespace(resolve_gpu_identity=resolve)}),patch.dict(ns,{'fixed_helper_capability':query}):result=ns['freeze_inputs'](stage,manifest,dstlib,rewrite=rewrite,admission_root=root)
   self.assertEqual(result['fixed_helper_seed_schema'],expected);self.assertEqual(result['worker'],'bin/game-pso-preparer');self.assertEqual([e[0]for e in events],['rewrite','actual-query']);self.assertEqual(list((root/'state').iterdir()),[])
 def test_old_worker_cannot_be_authorized_by_claimed_manifest(self):self.run_case(a.worker_old,1,0)
 def test_new_actual_output_wins_without_manifest_claim(self):self.run_case(a.worker_new,0,1)
if __name__=='__main__':unittest.main(verbosity=2)
