"""Scheduling reuse of source-validated modules; never native import authority.

The existing controller retains target discovery, new captures, native misses
and final receipt checks. Only redundant original-container extraction is
skipped with unchanged module bytes AND continuous readonly-source history.
"""
from pathlib import Path
import json,os,re,stat
from launcher_game_catalog import directory,digest,file_sha,write_json
from game_pso_owned_audit import checked_bytes
from incremental_file_hashes import replaceable,stat_stamp
from startup_change_journal import query,unchanged

MAX_RECEIPT=16*1024*1024
CASE=re.compile(r'module-[0-9a-f]{64}-[0-9a-f]{16}')

class ModuleProducts:
    def __init__(self,*,root,source,compiler,catalog,check):
        self.root=directory(root);self.source=directory(source);self.check=check
        self.readonly_source=stat.S_IMODE(self.source.stat().st_mode)==0o500
        tools=Path(__file__).parent
        self.binding=dict(schema=2,compiler=compiler,source=str(self.source),
            catalog=file_sha(catalog,check)[0],validators={name:file_sha(tools/name,check)[0]for name in
                ('module_product_schedule.py','run_game_module_contracts.py','run_owned_geometry_contracts.py',
                 'complete_geometry_psos.py','game_module_inputs.py','game_shader_catalog.py',
                 'gnm_shader_metadata.py','decode_shader_cache.py','launcher_game_modules.py','game_companion_modules.py')})
        self.path=self.root.parent/('module-products-'+compiler+'-'+digest(self.binding)+'.json')
        self.source_token=query([self.source]);self.prior={};self.verified={};self.hits=0
        self.native_prior={};self.native_verified={};self.native_hits=0
        try:
            info=self.path.lstat()
            if info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:return
            saved=json.loads(checked_bytes(self.path,limit=MAX_RECEIPT)[0]);body=saved['body']
            if set(saved)!={'body','sha256'}or digest(body)!=saved['sha256']or body['binding']!=self.binding:return
            if not self.readonly_source or not unchanged(body['source_journal'],[self.source]):return
            if not isinstance(body['verified'],dict)or len(body['verified'])>100000:return
            native=body.get('native_verified',{})
            if not isinstance(native,dict)or len(native)>100000:return
            self.prior=body['verified'];self.native_prior=native;self.source_token=body['source_journal']
        except(OSError,ValueError,KeyError,TypeError):pass

    def identity(self,entry):
        # No reference can hide in the skipped administrative/inventory area.
        packet=Path(entry['packet']);case=packet.parent.parent
        if case.parent!=self.root or not CASE.fullmatch(case.name):return None
        if packet!=case/'input/packet.json'or Path(entry['receipt'])!=case/'output/result.json'or Path(entry['spirv'])!=case/'output/module.spv':return None
        return digest(entry)

    def packet(self,uid,entry):
        self.check();key=self.identity(entry)
        if key is None or self.prior.get(uid)!=key:return None
        path=Path(entry['packet'])
        info=path.lstat()
        if info.st_nlink!=1 or info.st_mode&0o022:raise ValueError('linked or writable module packet')
        packet=json.loads(checked_bytes(path,limit=256*1024,expected=entry['packet_sha256'])[0])
        # Keep all actual frozen input byte/length/path checks. The only omitted
        # work is re-extracting the SAME code from its unchanged source archive.
        total=0
        for name in ('code','profile','spec','flat','expected_spirv','walker'):
            ref=packet[name]
            if ref is None:
                if name!='walker'or packet['walker_bytes']!=0:raise ValueError('module null input')
                continue
            p=Path(ref['path'])
            if p.parent!=path.parent or p.name!=ref['name']:raise ValueError('module frozen input root')
            info=p.lstat()
            if info.st_nlink!=1 or info.st_mode&0o022:raise ValueError('linked or writable module input')
            raw,_=checked_bytes(p,expected=ref['sha256']);total+=len(raw)
            if len(raw)!=ref['bytes']or total>2*1024*1024:raise ValueError('module frozen input extent')
        self.hits+=1;return packet

    def record(self,uid,entry):
        key=self.identity(entry)
        if key is not None:self.verified[uid]=key

    def _legacy_case(self,entry):
        """One indexed generic module from the SAME compiler, not a search root.

        COW cache migration retains original packet/receipt paths. The normal
        full validator already admits these; retain that real provenance rather
        than rewriting packets or receipts to manufacture local-path matches.
        """
        try:
            packet=Path(entry['packet']).absolute();case=packet.parent.parent;root=case.parent
            if root==self.root:return None
            if not CASE.fullmatch(case.name)or packet!=case/'input/packet.json':return None
            if Path(entry['receipt'])!=case/'output/result.json'or Path(entry['spirv'])!=case/'output/module.spv':return None
            if root.name!=self.root.name or root.parent.name!=self.root.parent.name or self.root.parent.name!='automatic-game-inputs':return None
            def state(gpu):
                if gpu.name=='arm64-native-v1':gpu=gpu.parent
                if not re.fullmatch(r'gui-gpu-local-aot-[0-9]+',gpu.name):raise ValueError('registered GPU root')
                return gpu.parent
            if state(root.parent.parent)!=state(self.root.parent.parent):return None
            return case
        except(OSError,ValueError,KeyError,TypeError):return None

    def _native_pin(self,path,entry=None):
        path=Path(path).absolute()
        allowed=(self.root,self.root.parent.parent/'cache')
        legacy=self._legacy_case(entry)if entry is not None else None
        if legacy is not None:allowed+= (legacy,)
        if path!=path.resolve(strict=True)or not any(path.is_relative_to(r)for r in allowed):raise ValueError('native module proof scope')
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode)or info.st_uid!=os.getuid()or info.st_nlink!=1 or info.st_mode&0o022 or not 0<info.st_size<=2*1024*1024:raise ValueError('native module proof file')
        return stat_stamp(info)

    def native_key(self,kind,entry,current):
        return digest(dict(schema=1,kind=kind,entry=entry,current=current))

    def _native_directory_pin(self,path,entry=None):
        path=Path(path).absolute()
        legacy=self._legacy_case(entry)if entry is not None else None
        if path!=path.resolve(strict=True)or path==self.root or not(path.is_relative_to(self.root)or legacy is not None and path.is_relative_to(legacy)):raise ValueError('native module directory scope')
        info=path.lstat()
        if not stat.S_ISDIR(info.st_mode)or info.st_uid!=os.getuid()or info.st_mode&0o022:raise ValueError('native module directory')
        return stat_stamp(info)

    def reuse_native(self,kind,entry,current):
        """Reuse only a successful exact contract with unchanged entire read set.

        Source continuity is checked at load and again before phase publication.
        Changed files go through the original complete validator, never a fake
        successful native result. No runtime importer uses this proof.
        """
        key=self.native_key(kind,entry,current)
        row=self.native_verified.get(key,self.native_prior.get(key))
        try:
            if row is None or not self.readonly_source:return False
            files=row['files']
            if set(row)!={'files','directories'}or not isinstance(files,dict)or not 1<=len(files)<=128:return False
            dirs=row['directories']
            legacy=self._legacy_case(entry)
            expected={str(Path(p).parent)for p in files if Path(p).parent.is_relative_to(self.root)or legacy is not None and Path(p).parent.is_relative_to(legacy)}
            if not isinstance(dirs,dict)or set(dirs)!=expected:return False
            if any(self._native_directory_pin(p,entry)!=pin for p,pin in dirs.items()):return False
            for path,pin in files.items():
                self.check()
                if self._native_pin(path,entry)!=pin:return False
            self.native_verified[key]=row;self.native_hits+=1;return True
        except(OSError,ValueError,KeyError,TypeError):return False

    def begin_native(self,kind,entry,current,paths):
        try:
            if not self.readonly_source:return None
            files={str(Path(p).absolute()):self._native_pin(p,entry)for p in paths}
            if not 1<=len(files)<=128:return None
            legacy=self._legacy_case(entry)
            dirs={str(Path(p).parent):self._native_directory_pin(Path(p).parent,entry)for p in files if Path(p).parent.is_relative_to(self.root)or legacy is not None and Path(p).parent.is_relative_to(legacy)}
            return self.native_key(kind,entry,current),dict(files=files,directories=dirs),entry
        except(OSError,ValueError,KeyError,TypeError):return None

    def record_native(self,attempt):
        """Call only AFTER the original successful full semantic validation."""
        if attempt is None:return False
        key,row,entry=attempt
        try:
            if any(self._native_pin(p,entry)!=pin for p,pin in row['files'].items()):return False
            if any(self._native_directory_pin(p,entry)!=pin for p,pin in row['directories'].items()):return False
            self.native_verified[key]=row;return True
        except(OSError,ValueError,KeyError,TypeError):return False

    def publish(self):
        self.check()
        stable_source=unchanged(self.source_token,[self.source])
        if (self.hits or self.native_hits)and not stable_source:raise ValueError('registered source changed during module reuse')
        if not self.readonly_source or not(self.verified or self.native_verified)or not stable_source or not replaceable(self.path):return False
        # This is a per-input SOURCE proof. Packet SHA and every frozen input
        # are rechecked on use, while the original controller still checks the
        # native receipt/SPIR-V/nonce/cohort. Unrelated products and report files
        # cannot invalidate an already proved packet. No whole-cache read pass.
        body=dict(binding=self.binding,source_journal=self.source_token,
            verified={**self.prior,**self.verified},native_verified={**self.native_prior,**self.native_verified})
        saved=dict(body=body,sha256=digest(body))
        if len(json.dumps(saved,separators=(',',':')).encode())>MAX_RECEIPT:return False
        write_json(self.path,saved);return True
