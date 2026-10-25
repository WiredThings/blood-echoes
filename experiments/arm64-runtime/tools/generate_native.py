#!/usr/bin/env python3
"""Generate ABI-exact host C shims from Clang's parsed declarations, no size guessing."""
from pathlib import Path
import subprocess,json,re
from native_abi_declarations import is_guest_definition
P=Path(__file__).resolve().parents[1]
R=P/'vendor/bb_runtime';G=P/'generated';G.mkdir(exist_ok=True)
names=['runtime','runtime_thread','runtime_kernel','runtime_memory','runtime_mutex','runtime_sema','runtime_rwlock','runtime_time','runtime_content','runtime_file','runtime_services','runtime_savedata','runtime_rtc','runtime_ajm','runtime_audio','runtime_pad']
metadata=[];calls=[]
# Clang gives physical ABI types, but C void* declarations cannot express code
# addresses or opaque user tokens. These slots are never dereferenced as data.
opaque_slots={('runtime_services','http_wait'):{0},
 ('runtime_services','netctl_register'):{0,1},
 ('runtime_services','np_register'):{0,1},
 ('runtime_services','np_register_void'):{0,1},
 ('runtime_thread','thread_create'):{3},
 ('runtime_thread','posix_create'):{3},
 ('runtime_thread','posix_create_name'):{3},
 ('runtime_thread','thread_exit'):{0},
 ('runtime_ajm','job_control'):{3,5,7},
 ('runtime_ajm','job_run'):{3,5,7,9},
 ('runtime_ajm','job_run_split'):{7,9}}
# VM addresses are not C data buffers: PROT_NONE/readonly/reserved mappings
# must reach the real VM operation and its ownership/errno checks. In/out and
# query result pointers remain fully validated as data.
opaque_slots.update({('runtime_memory',n):{0} for n in [
 'direct_unmap','release_flexible','posix_mmap','kernel_mprotect','query_protection','virtual_query']})
callback_slots={('runtime_services',n):{0} for n in ['netctl_register','np_register','np_register_void']}
# These scalar pointer words are proved by explicit count-aware wrapper checks.
# Avoid repeating the generic one-byte check before proving the full span.
span_slots={('runtime',n):({0}if n=='guest_memset'else{0,1})for n in ['guest_memcpy','guest_memmove','guest_memset','guest_memcmp']}

def typ(t):
    raw=t.get('desugaredQualType',t['qualType'])
    if '*' in raw:
        if '(*' in raw or '(*)' in raw:return ('uint64_t','Q')
        # const applies to the immediate pointee, not recursively: const void**
        # writes a pointer slot and is mutable, whereas const void* reads bytes.
        pointee=raw[:raw.rfind('*')].strip()
        readonly=pointee.endswith('const') or ('*' not in pointee and 'const' in pointee.split())
        return ('const void*','R') if readonly else ('void*','P')
    raw=raw.replace('const ','').strip()
    kind={'void':'v','_Bool':'b','char':'c','signed char':'c','unsigned char':'C','short':'s','unsigned short':'S','int':'i','unsigned int':'I','long':'l','unsigned long':'L','long long':'q','unsigned long long':'Q','float':'f','double':'d'}.get(raw)
    if not kind:raise ValueError(raw)
    return ('bool' if raw=='_Bool' else raw,kind)
for name in names:
    source=R/'src'/f'{name}.c';text=source.read_bytes().decode('latin1')
    cmd=['clang','-arch','arm64','-std=c11','-DBB_GENERATING_NATIVE_ABI=1','-I/opt/homebrew/include','-Wno-ignored-attributes','-I'+str(R),'-I'+str(R/'src'),'-I'+str(P/'src'),'-Xclang','-ast-dump=json','-fsyntax-only',str(source)]
    result=subprocess.run(cmd,capture_output=True,text=True)
    if result.returncode:raise RuntimeError(name+'\n'+result.stderr[-5000:])
    tree=json.loads(result.stdout);body=['// Generated from Clang AST.','#include <stdbool.h>','#include "runtime_exit_codes.h"',' #include "'+str(source)+'"']
    registered=[]
    for node in tree.get('inner',[]):
        # Compiler-proven ABI declaration, including all current/future macro names.
        if not is_guest_definition(node) or node.get('variadic'):continue
        fname=node['name']
        params=[x for x in node.get('inner',[]) if x.get('kind')=='ParmVarDecl']
        q=node['type']['qualType'];ret=q[:q.index('(')].strip()
        # Return typedefs in this runtime are integer typedefs or pointers.
        aliases={'uint64_t':'unsigned long long','int64_t':'long long','uint32_t':'unsigned int','int32_t':'int','uint16_t':'unsigned short','int16_t':'short','uintptr_t':'unsigned long','size_t':'unsigned long','ssize_t':'long','uint8_t':'unsigned char','int8_t':'signed char'}
        try:
            rt,rk=typ({'qualType':aliases.get(ret,ret)})
            args=[typ(x['type']) for x in params]
            for i in opaque_slots.get((name,fname),set()):args[i]=('uint64_t','Q')
            for i in span_slots.get((name,fname),set()):args[i]=('uint64_t','Q')
        except ValueError as e:
            metadata.append(dict(source=name,function=fname,unsupported=str(e)));continue
        wrapper=f'bb_native_{name}_{fname}';signature=rk+''.join(k for _,k in args)
        declarations=','.join(f'{t} a{i}' for i,(t,_) in enumerate(args)) or 'void'
        arguments=','.join('('+x['type']['qualType']+')a'+str(i) for i,x in enumerate(params))
        if (name,fname)in {('runtime','guest_libc_exit'),('runtime_kernel','hard_exit')}:arguments='bb_arm_guest_exit_status(a0)'
        checks=[f'if(a{i})bb_arm_require_callback("{name}.{fname}.arg{i}",(uintptr_t)a{i});' for i in callback_slots.get((name,fname),set())]
        for i,param in enumerate(params):
            raw=param['type'].get('desugaredQualType',param['type']['qualType'])
            if '*' not in raw or '(*' in raw or '(*)' in raw:continue
            if i in opaque_slots.get((name,fname),set()):continue
            if raw.replace(' ','')=='constchar*':
                limit="SIZE_MAX" if fname=="guest_strlen" else "4096"
                checks.append(f'if(a{i})bb_arm_require_string("{name}.{fname}.arg{i}",(const char*)a{i},{limit});')
            elif raw.replace(' ','') not in ['void*','constvoid*']:
                checks.append(f'if(a{i})bb_arm_require_range("{name}.{fname}.arg{i}",(uintptr_t)a{i},sizeof(*(({param["type"]["qualType"]})0)),{0 if args[i][1]=="R" else 1});')
        if name=='runtime' and fname in ['guest_memcpy','guest_memmove','guest_memset','guest_memcmp']:
            # void* alone conveys only one byte to the generic ABI adapter.
            # Prove the complete count before the existing host libc/notify path.
            checks.append(f'if(a2)bb_arm_require_range("{name}.{fname}.span0",(uintptr_t)a0,a2,{0 if fname=="guest_memcmp" else 1});')
            if fname!='guest_memset':
                checks.append(f'if(a2)bb_arm_require_range("{name}.{fname}.span1",(uintptr_t)a1,a2,0);')
        if name=='runtime_file' and fname in ['sce_read','posix_read','sce_pread','posix_pread','sce_write','posix_write','sce_pwrite','posix_pwrite','sce_getdents','posix_getdents']:
            checks.append(f'if(a2)bb_arm_require_range("{name}.{fname}.buffer",(uintptr_t)a1,a2,{0 if "write" in fname else 1});')
        if name=='runtime_ajm' and fname in ['job_control','job_run']:
            return_slot=7 if fname=='job_control' else 9
            size=48 if fname=='job_control' else 64
            checks.append(f'bb_arm_require_range("{name}.{fname}.builder",(uintptr_t)a0,{size}+(a{return_slot}?16:0),1);')
        if name=='runtime_ajm' and fname=='job_run_split':
            checks.append('if(a4>UINT32_MAX/16||a6>UINT32_MAX/16)bb_arm_require_range("ajm.split.count",0,1,0);')
            checks.append('if(a4)bb_arm_require_range("ajm.split.in",(uintptr_t)a3,a4*sizeof(AjmBuffer),0);if(a6)bb_arm_require_range("ajm.split.out",(uintptr_t)a5,a6*sizeof(AjmBuffer),0);')
            checks.append('bb_arm_require_range("ajm.split.builder",(uintptr_t)a0,32+(a9?16:0)+16*(a4+a6),1);')
        if name=='runtime_ajm' and fname=='job_inline':
            checks.append('if(a2>UINT32_MAX-15)bb_arm_require_range("ajm.inline.size",0,1,0);bb_arm_require_range("ajm.inline.builder",(uintptr_t)a0,8+((a2+7)&~UINT64_C(7)),1);if(a2)bb_arm_require_range("ajm.inline.data",(uintptr_t)a1,a2,0);')
        body.append(f'{rt} {wrapper}({declarations})'+' { '+''.join(checks)+('' if rk=='v' else 'return ('+rt+')')+f'{fname}({arguments});'+' }')
        registered.append(f'bb_arm_add_native("{fname}",(void*){fname},(void*){wrapper},"{signature}");')
        metadata.append(dict(source=name,function=fname,signature=signature,parameters=[x['type']['qualType'] for x in params],returns=ret,full_span_pointer_slots=sorted(span_slots.get((name,fname),set()))))
    body.append(f'void bb_arm_register_{name}(void)'+' { '+' '.join(registered)+' }')
    (G/f'{name}_bridge.c').write_text('\n'.join(body)+'\n');calls.append(f'bb_arm_register_{name}')
(G/'native_registry.c').write_text('#include "arm_bridge.h"\n'+''.join(f'void {c}(void);\n' for c in calls)+'void bb_arm_runtime_register(void) {'+''.join(c+'();' for c in calls)+'}\n')
(G/'native-signatures.json').write_text(json.dumps(metadata,indent=2))
types={'v':'void','b':'bool','c':'signed char','C':'unsigned char','s':'short','S':'unsigned short','i':'int','I':'unsigned int','l':'long','L':'unsigned long','q':'long long','Q':'unsigned long long','f':'float','d':'double','P':'void*','R':'const void*'}
cases=[]
for sig in sorted({m['signature'] for m in metadata if 'signature'in m}):
    function=types[sig[0]]+' (*)('+','.join(types[k] for k in sig[1:])+')'
    cases.append('if(signature=="'+sig+'") return MakeHleCallAdapter(reinterpret_cast<'+function+'>(wrapper));')
(G/'native_adapters.inc').write_text('\n'.join(cases)+'\n')
print('Native wrappers:',sum('signature' in x for x in metadata),'unsupported:',sum('unsupported'in x for x in metadata))
