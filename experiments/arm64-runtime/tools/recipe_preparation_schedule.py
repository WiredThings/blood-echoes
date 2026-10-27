"""Per-required-recipe scheduling after actual native verification.

Never fabricates a worker result or changes a native key. Reused recipes are
reported separately; final runtime source/function/descriptor admission remains.
The queue commits a candidate only after its real library-environment check.
"""
from pathlib import Path
import hashlib,json,os,re,stat,uuid
from launcher_game_catalog import directory,file_sha,digest
from game_pso_owned_audit import checked_bytes
from game_pso_progress_identity import input_identity
from resource_parse_products import Products
from local_metal_builder import Refused as LocalRefused,Stopped as LocalStopped

HEX=re.compile(r'[0-9a-f]{64}')
MAX_FILE=128*1024*1024
MAX_TOTAL=256*1024*1024
MAX_FILES=128

def need(v,m):
    if not v:raise ValueError(m)

def pin(path):
    p=Path(path).absolute();need(p==p.resolve(strict=True),'canonical recipe dependency')
    s=p.lstat();need(stat.S_ISREG(s.st_mode)and s.st_uid==os.getuid()and s.st_nlink==1
        and not(s.st_mode&0o022)and 0<s.st_size<=MAX_FILE,'owned bounded recipe dependency')
    return [s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns]

class Recipes:
    def __init__(self,*,context,environment,gpu,artifact,progress,code_identity,check):
        self.gpu=directory(gpu);self.artifact=directory(artifact);self.check=check
        self.local={k:v for k,v in environment.items()if k.startswith('BB_MTL_LOCAL_AOT_')}
        from quick_restart_preparation import local_selection
        cohort=self.local.get('BB_MTL_LOCAL_AOT_COHORT','')
        need(local_selection(self.local,self.gpu,cohort)is not None,'verified current local selection required')
        self.root=directory(self.local['BB_MTL_LOCAL_AOT_ROOT']);self.cohort=cohort;self.build=self.local['BB_MTL_LOCAL_AOT_BUILD_ID']
        self.requests=directory(self.root/cohort/'requests');self.libraries=directory(self.root/cohort/'artifacts'/self.build)
        need(self.artifact.is_relative_to(self.gpu/'cache'),'selected native artifact scope')
        self.scope=dict(schema=1,context=context,local=self.local,code=code_identity)
        self.store=Products(directory(progress)/'required-recipe-schedules',digest(code_identity))
        self.session=uuid.uuid4().hex;self.memo={};self.stats=dict(reused=0,published=0,misses=0,refused=0)

    def inputs(self,unit,*,candidate=False):
        need(unit.get('record_type')=='offline-pso-input'and 'owned_geometry_pso'not in unit,'original plain recipe protocol')
        identity=input_identity(unit)
        need(directory(identity['cache']).is_relative_to(self.gpu/'cache'),'recipe cache outside selected GPU')
        policy=unit['execution_policy']
        need(policy['runtime_cache_import_allowed']is False and policy['actual_final_read_admission']['allowed']is False
            and policy['candidate_preparation']['policy_eligible']is True
            and unit['source_provenance']['enabled']is False and unit['profile']['compiler_compatibility_proof']is False,'original native candidate policy required')
        value=dict(kind='verified-required-recipe-schedule-v1',scope=self.scope,unit=unit)
        if candidate:value['uncommitted_session']=self.session
        return value

    def proof_file(self,path,expected=None,size=None):
        self.check();p=Path(path);before=pin(p)
        need(p.is_relative_to(self.gpu/'cache')or p.is_relative_to(self.requests)or p.is_relative_to(self.libraries),'recipe dependency scope')
        old=self.memo.get(str(p))
        if old is not None and old['pin']==before:row=old
        else:
            sha,n=file_sha(p,self.check);need(pin(p)==before and n==before[6],'recipe dependency changed during proof')
            row=dict(path=str(p),pin=before,sha256=sha,bytes=n)
            # Bounded invocation-only hash memo; no game-time residency/cache cap.
            if len(self.memo)<8192:self.memo[str(p)]=row
        need(expected is None or row['sha256']==expected,'recipe dependency digest mismatch')
        need(size is None or row['bytes']==size,'recipe dependency size mismatch')
        return row

    def current_files(self,files):
        need(isinstance(files,list)and 0<len(files)<=MAX_FILES,'recipe proof file count')
        total=0;seen=set()
        for row in files:
            need(set(row)=={'path','pin','sha256','bytes'}and isinstance(row['sha256'],str)and HEX.fullmatch(row['sha256']),'complete recipe file proof')
            p=Path(row['path']);need(p.is_relative_to(self.gpu/'cache')or p.is_relative_to(self.requests)or p.is_relative_to(self.libraries),'recipe proof scope')
            need(type(row['bytes'])is int and 0<row['bytes']<=MAX_FILE and isinstance(row['pin'],list)and len(row['pin'])==9
                and all(type(v)is int for v in row['pin'])and row['pin'][6]==row['bytes'],'recipe proof bounds')
            need(row['path']not in seen,'duplicate recipe dependency');seen.add(row['path'])
            total+=row['bytes'];need(total<=MAX_TOTAL,'recipe dependency byte budget')
            # Matching mutation-sensitive metadata reuses the prior real byte
            # proof; a changed leaf must match its exact original bytes again.
            if pin(row['path'])!=row['pin']:self.proof_file(row['path'],row['sha256'],row['bytes'])
        return True

    def reuse(self,unit):
        try:
            value=self.store.get(self.inputs(unit))
            if value is None:self.stats['misses']+=1;return None
            need(set(value)=={'schema','files','native_verification','runtime_artifact_authority'},'recipe proof fields')
            need(type(value['schema'])is int and value['schema']==1 and value['native_verification']=='actual-worker-and-stable-environment'
                and value['runtime_artifact_authority']is False,'actual committed recipe verification required')
            self.current_files(value['files']);self.stats['reused']+=1
            return dict(status='required-recipe-schedule-reused',previous_native_verification=True,
                native_worker_invoked=False,runtime_artifact_authority=False,proof_identity=digest(value))
        except(OSError,ValueError,KeyError,TypeError):self.stats['refused']+=1;return None

    def observe(self,unit,row,local_keys):
        """Record only actual worker events; not ready until queue commit."""
        try:
            from game_pso_queue import local_preparation_ready
            from prepare_game_psos import preparation_ready
            from local_metal_builder import load_request,read_json,canonical
            need(row.get('exit')==0 and local_preparation_ready(row,preparation_ready),'real successful local native recipe required')
            inputs=self.inputs(unit,candidate=True);files={}
            def remember(p,sha=None,size=None):
                proof=self.proof_file(p,sha,size);files[proof['path']]=proof
            refs=[unit['profile']['reference'],unit['key']['reference']]
            for stage in unit['stages']:refs.extend((stage['metadata'],stage['spirv']))
            for ref in refs:remember(Path(ref['path']),ref['sha256'],ref['bytes'])
            need(isinstance(local_keys,list)and 0<len(local_keys)<=16 and all(isinstance(k,str)and HEX.fullmatch(k)for k in local_keys),'actual bounded local request events required')
            for key in set(local_keys):
                original=None
                for folder in(self.requests,self.requests/key[:2]):
                    if not folder.exists():continue
                    directory(folder)
                    has=[os.path.lexists(folder/(key+suffix))for suffix in('.json','.metal')]
                    if not any(has):continue
                    need(all(has),'unpaired local request dependency')
                    request,source=load_request(folder,key,self.cohort)
                    need(original is None or original==request,'conflicting local request copies');original=request
                    remember(folder/(key+'.json'));remember(folder/(key+'.metal'),request['source_sha256'],len(source));del source
                need(original is not None,'native local request missing')
                remember(self.libraries/'build.json')
                blob=self.proof_file(self.libraries/(key+'.metallib'))
                receipt=read_json(self.libraries/(key+'.json'))
                need(canonical(receipt)==canonical(dict(original,build_id=self.build,library_sha256=blob['sha256'],library_bytes=blob['bytes'])),'current native library receipt mismatch')
                files[blob['path']]=blob;remember(self.libraries/(key+'.json'))
            roots=[]
            for p in self.artifact.iterdir():
                if p.name.startswith('native-pipeline-artifacts-v1-')and HEX.fullmatch(p.name.removeprefix('native-pipeline-artifacts-v1-')):
                    roots.append(directory(p));need(len(roots)<=64,'native artifact location lookup budget')
            for event in row['artifact_events']:
                key=event['key'];matches=[]
                for root in roots:
                    receipt_path=root/(key+'.json')
                    if not os.path.lexists(receipt_path):continue
                    receipt=json.loads(checked_bytes(receipt_path,limit=4096)[0]);prep=root.name.removeprefix('native-pipeline-artifacts-v1-')
                    need(type(receipt.get('schema'))is int and receipt['schema']==1 and receipt['recipe_key']==key and receipt['preparation']==prep,'native artifact receipt identity')
                    name=receipt['archive'];need(isinstance(name,str)and len(name)<=240 and name.startswith(key+'-')and name.endswith('.binaryarchive')and '/'not in name and '\\'not in name and '..'not in name,'native archive path')
                    if receipt['archive_sha256']!=event['archive_sha256']:continue
                    remember(receipt_path);remember(root/name,event['archive_sha256'],receipt['archive_bytes']);matches.append(root)
                need(len(matches)==1,'unambiguous actual native artifact required')
            proof=dict(schema=1,files=list(files.values()),native_verification='pending-real-environment-check',runtime_artifact_authority=False)
            self.current_files(proof['files']);self.store.put(inputs,proof)
        except(OSError,ValueError,KeyError,TypeError,LocalRefused)as error:
            if isinstance(error,LocalStopped):raise
            self.stats['refused']+=1

    def commit(self,unit):
        """Only called after the existing full phase environment check passed."""
        try:
            proof=self.store.get(self.inputs(unit,candidate=True))
            if proof is None:return False
            need(proof.get('native_verification')=='pending-real-environment-check','pending current-session native candidate required')
            self.current_files(proof['files']);proof['native_verification']='actual-worker-and-stable-environment'
            if self.store.put(self.inputs(unit),proof):self.stats['published']+=1;return True
        except(OSError,ValueError,KeyError,TypeError):self.stats['refused']+=1
        return False

    def close(self):self.store.close();self.memo.clear()
