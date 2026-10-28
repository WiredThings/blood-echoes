"""Authored identity and old-artifact admission; no actual compiler/game."""
from pathlib import Path
from unittest.mock import patch
import copy,os,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import local_metal_builder as b

def version(token='NBhkO4'):
 return 'Apple metal version 32023.921 (metalfe-32023.921.6)\nTarget: air64-apple-darwin27.0.0\nThread model: posix\nInstalledDir: /private/var/run/com.apple.security.cryptexd/mnt/com.apple.MobileAsset.MetalToolchain-v27.1.266.1.'+token+'/Metal.xctoolchain/usr/metal/current/bin'

class Guard:
 def check(self,**kw):pass

class MountIdentity(unittest.TestCase):
 def manifest(self):
  return dict(schema=2,recipe=b.RECIPE,tools={k:dict(sha256=str(n)*64,version=version()if k=='metal'else k+' real version')for n,k in enumerate(('metal','metallib','xcrun'),1)},sdk=dict(schema=1,metadata={'SDKSettings.json':dict(sha256='4'*64,bytes=4)}),os_build='REAL-OS')
 def test_only_random_mount_token_is_removed(self):
  self.assertEqual(b.stable_metal_version(version()),b.stable_metal_version(version('CLrnEw')))
  a=self.manifest();before=copy.deepcopy(a);stable=b.stable_build_manifest(a)
  self.assertEqual(a,before);self.assertEqual(stable,b.stable_build_manifest(stable))
 def test_unknown_layout_or_extra_version_fields_stay_exact(self):
  for value in(version()+'\nextra ABI: 2',version().replace('usr/metal/current','usr/metal/other'),version().replace('.NBhkO4/','.short/'),version().replace('InstalledDir:','OtherDir:')):
   self.assertEqual(value,b.stable_metal_version(value))
 def test_all_real_compatibility_fields_remain_distinct(self):
  original=self.manifest();stable=b.stable_build_manifest(original)
  for field in('sha','metal-version','target','asset-version','metallib','xcrun','SDK','OS','recipe','unknown'):
   x=copy.deepcopy(original)
   if field=='sha':x['tools']['metal']['sha256']='9'*64
   elif field=='metal-version':x['tools']['metal']['version']=version().replace('32023.921','32023.922')
   elif field=='target':x['tools']['metal']['version']=version().replace('darwin27','darwin28')
   elif field=='asset-version':x['tools']['metal']['version']=version().replace('v27.1.266.1','v27.1.267.1')
   elif field in('metallib','xcrun'):x['tools'][field]['version']+=' changed'
   elif field=='SDK':x['sdk']['metadata']['SDKSettings.json']['sha256']='8'*64
   elif field=='OS':x['os_build']+='changed'
   elif field=='recipe':x['recipe']+='changed'
   else:x['future_abi']='changed'
   with self.subTest(field=field):self.assertNotEqual(stable,b.stable_build_manifest(x))
 def fixture(self,root):
  request=dict(schema=2,cohort='a'*64,source_sha256=b.digest(b'authored source'),language='metal3.1',math_mode='safe',math_fp32='fast',recipe=b.LIBRARY_RECIPE)
  request['key']=b.derive_key(request);old=self.manifest();oid=b.digest(b.canonical(old));folder=root/oid;folder.mkdir(mode=0o700)
  b.write_new(folder/'build.json',b.canonical(old));blob=b'authored metallib'
  b.write_new(folder/(request['key']+'.metallib'),blob);b.write_new(folder/(request['key']+'.json'),b.canonical(b.expected_receipt(request,oid,blob)))
  return request,old,oid,folder,blob
 def test_old_manifest_and_complete_receipt_adopt_without_modifying_original(self):
  with tempfile.TemporaryDirectory()as raw:
   root=Path(raw);request,old,oid,folder,blob=self.fixture(root);before={p.name:(p.read_bytes(),p.stat().st_ino,p.stat().st_mtime_ns)for p in folder.iterdir()}
   stable=b.stable_build_manifest(old);c=b.equivalent_builds(root,b.digest(b.canonical(stable)),stable,Guard());self.assertEqual(len(c),1)
   result=b.retained_mount_product(c,request,Guard());self.assertEqual(result[0],blob);self.assertEqual(result[1]['original_build_manifest'],old)
   self.assertEqual(before,{p.name:(p.read_bytes(),p.stat().st_ino,p.stat().st_mtime_ns)for p in folder.iterdir()})
 def test_corrupt_manifest_wrong_digest_and_unknown_fields_reject(self):
  for change in('corrupt','digest','SDK','extra'):
   with self.subTest(change=change),tempfile.TemporaryDirectory()as raw:
    root=Path(raw);request,old,oid,folder,blob=self.fixture(root);stable=b.stable_build_manifest(old)
    if change=='corrupt':(folder/'build.json').write_bytes(b'{')
    elif change=='digest':folder.rename(root/('9'*64))
    else:
     x=copy.deepcopy(old);x['sdk'if change=='SDK'else'future']='changed';(folder/'build.json').write_bytes(b.canonical(x));folder.rename(root/b.digest(b.canonical(x)))
    self.assertEqual(b.equivalent_builds(root,b.digest(b.canonical(stable)),stable,Guard()),[])
 def test_corrupt_library_receipt_and_changed_manifest_after_discovery_reject(self):
  for change in('library','receipt','manifest','symlink'):
   with self.subTest(change=change),tempfile.TemporaryDirectory()as raw:
    root=Path(raw);request,old,oid,folder,blob=self.fixture(root);stable=b.stable_build_manifest(old);c=b.equivalent_builds(root,b.digest(b.canonical(stable)),stable,Guard())
    if change=='library':(folder/(request['key']+'.metallib')).write_bytes(b'corrupt')
    elif change=='receipt':(folder/(request['key']+'.json')).write_bytes(b'{}')
    elif change=='manifest':(folder/'build.json').write_bytes(b'{}')
    else:
     lib=folder/(request['key']+'.metallib');lib.rename(root/'outside');lib.symlink_to(root/'outside')
    self.assertIsNone(b.retained_mount_product(c,request,Guard()))
 def test_full_native_options_and_source_key_are_not_relaxed(self):
  with tempfile.TemporaryDirectory()as raw:
   root=Path(raw);request,old,oid,folder,blob=self.fixture(root);stable=b.stable_build_manifest(old);c=b.equivalent_builds(root,b.digest(b.canonical(stable)),stable,Guard())
   for field in('source_sha256','math_mode','math_fp32','language','cohort'):
    x=dict(request);x[field]={'source_sha256':'8'*64,'math_mode':'relaxed','math_fp32':'precise','language':'metal3.2','cohort':'b'*64}[field];x['key']=b.derive_key(x)
    with self.subTest(field=field):self.assertIsNone(b.retained_mount_product(c,x,Guard()))
 def test_stop_is_never_converted_to_reuse(self):
  class Stop:
   def check(self,**kw):raise b.Stopped('authored stop')
  with tempfile.TemporaryDirectory()as raw:
   root=Path(raw);request,old,oid,folder,blob=self.fixture(root)
   with self.assertRaises(b.Stopped):b.equivalent_builds(root,'0'*64,b.stable_build_manifest(old),Stop())
 def test_live_selected_toolchain_mismatch_refuses(self):
  with patch.object(Path,'resolve',lambda p,**kw:p):
   with self.assertRaises(b.Refused):b.stable_metal_version(version(),selected='/another/toolchain/metal')

if __name__=='__main__':unittest.main()
