"""Authored x86 unaligned load under actual FEX, with and without signal tracing."""
from pathlib import Path
import hashlib,json,struct,subprocess,sys,time
from game_fixture import make
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from analyze_stutter import read
from qa_build_guard import require_no_games

def fixture(wrong=False):
 raw=bytearray(make());_,size,old_entry,ns,nr,ni,_=struct.unpack_from('<8s6Q',raw)
 offset=56;nm=struct.unpack_from('<Q',raw,offset+40)[0];offset+=48+56*nm
 nb=struct.unpack_from('<Q',raw,offset)[0];offset+=8+24*nb+24*ns+128*ni+32*nr
 entry=0x3000;address=0x4f01;sentinel=0x123456789abcdef
 # Execute the existing authored multi-thread/TLS harness first. FEX enables
 # atomic TSO after multiple guest threads; a single-thread RIP-relative load
 # alone need not use the faulting ordered-load path.
 code=bytearray.fromhex('48 83 ec 08 e8')+struct.pack('<i',old_entry-entry-9)
 code+=bytes.fromhex('85 c0 75 20')
 at=entry+len(code);code+=bytes.fromhex('48 8d 3d')+struct.pack('<i',address-at-7)
 code+=bytes.fromhex('48 8b 07 48 ba')+struct.pack('<Q',sentinel)+bytes.fromhex('48 39 d0 75 07 31 c0 48 83 c4 08 c3 b8 03 00 00 00 48 83 c4 08 c3')
 struct.pack_into('<Q',raw,16,entry)
 raw[offset+entry:offset+entry+len(code)]=code;struct.pack_into('<Q',raw,offset+address,sentinel+wrong)
 return raw

require_no_games();e=P/'evidence'/('signal-audit-fex-'+str(time.time_ns()));e.mkdir();rows=[]
for name,enabled,wrong,expected in [('off',False,False,0),('on',True,False,0),('wrong',True,True,2)]:
 private=P/'state'/(e.name+'-'+name);private.mkdir();(private/'gpu').mkdir();(private/'config.ini').write_text('');d=e/name;d.mkdir();boot=P/'fixtures'/(e.name+'-'+name+'.bbprobe');boot.write_bytes(fixture(wrong))
 env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(private),TMPDIR=str(private)+'/',BB_CONFIG=str(private/'config.ini'),BB_GPU_USER_DIR=str(private/'gpu'),BB_AUDIO='none',BB_ONLINE='0',BB_QUICKSAVE='0',BB_UNALIGNED_AUDIT='1'if enabled else'0',BB_STUTTER_AUDIT=str(d/'stutter'))
 r=subprocess.run([str(P/'build/fex/Bin/bb-game-arm64'),'--authored-fixture','--boot',str(boot),'--state',str(P/'state'/(e.name+'-'+name+'-runtime'))],env=env,capture_output=True,text=True,timeout=30)
 log=r.stdout+r.stderr;(d/'console.log').write_text(log);traces=[read(f)for f in d.glob('stutter-*.bin')];events=[row for t in traces for row in t['rows']if 73<=row['kind']<=77];fixed=log.count('BACHATA_UNALIGNED_FIX:');outer=[row for row in events if row['kind']==73];rejected=[row for row in events if row['kind']==77]
 assert r.returncode==expected,(name,r.returncode,log[-1200:]);assert not rejected
 assert (enabled and len(outer)==fixed)or(not enabled and not events),(name,fixed,len(outer))
 assert all(t['all_published']for t in traces)
 rows.append(dict(case=name,exit=r.returncode,alignment_fixes=fixed,alignment_rows=len(outer),actual_signal_exercised=bool(outer),trace_complete=True));print(name,r.returncode,fixed,len(outer),flush=True)
result=dict(passed=True,cases=rows,cpu_sha256=hashlib.file_digest((P/'build/fex/Bin/bb-game-arm64').open('rb'),'sha256').hexdigest(),game_started=False,formal_data_modified=False)
(e/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(e)
