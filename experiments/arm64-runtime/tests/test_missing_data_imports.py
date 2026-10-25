"""Real authored FEX data relocations; no assets, saves, window or game."""
from pathlib import Path
import argparse,hashlib,json,struct,subprocess,time
from game_fixture import make
P=Path(__file__).resolve().parents[1]

def fixture(mode):
    if mode=='function-missing':return make('missing-import')
    raw=bytearray(make('missing-import'))
    magic,size,entry,ns,nr,ni,caps=struct.unpack_from('<8s6Q',raw)
    offset=56;nm=struct.unpack_from('<Q',raw,offset+40)[0];offset+=48+56*nm
    nb=struct.unpack_from('<Q',raw,offset)[0];offset+=8+24*nb+24*ns
    names=[raw[offset+128*i:offset+128*(i+1)].split(b'\0')[0]for i in range(ni)]
    index=names.index(b'bb.prototype.missing')
    # __progname is now implemented; a stable synthetic unknown remains a negative contract.
    name=b'f7uOxY9mM1U#p#J'if mode=='valid-data'else b'bb.prototype.unknown.data'
    raw[offset+128*index:offset+128*(index+1)]=name.ljust(128,b'\0')
    relocs=offset+128*ni;slot=None
    for i in range(nr):
        at=relocs+32*i;t,k,n,a=struct.unpack_from('<4Q',raw,at)
        if k and n==index:
            struct.pack_into('<Q',raw,at+8,2);slot=t
            if mode=='read-last':struct.pack_into('<Q',raw,at+24,16384-8)
    assert slot is not None
    code=bytes.fromhex('31 c0 c3')
    if mode!='unused':
        operation=bytes.fromhex('48 89 00')if mode=='write'else bytes.fromhex('ff d0')if mode=='execute'else bytes.fromhex('48 8b 00')
        code=bytes.fromhex('48 8b 05')+struct.pack('<i',slot-entry-7)+operation+bytes.fromhex('31 c0 c3')
    image=relocs+32*nr;raw[image+entry:image+entry+len(code)]=code
    return raw

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--binary',type=Path);parser.add_argument('--expect-old-readable',action='store_true');a=parser.parse_args()
    binary=(a.binary or P/'build/fex/Bin/bb-game-arm64').resolve()
    assert binary.is_relative_to(P/'build')or binary.is_relative_to(P/'apps')
    E=P/'evidence'/('missing-data-regression-'+str(time.time_ns()));E.mkdir()
    scratch=P/'state'/('missing-data-test-'+str(time.time_ns()));scratch.mkdir();(scratch/'bbport.ini').write_text('');(scratch/'gpu').mkdir()
    env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(P/'state'),TMPDIR=str(scratch)+'/',BB_CONFIG=str(scratch/'bbport.ini'),BB_GPU_USER_DIR=str(scratch/'gpu'),BB_AUDIO='none',BB_ONLINE='0',BB_QUICKSAVE='0')
    cases=['unused','read','valid-data']if a.expect_old_readable else ['unused','read','read-last','write','execute','valid-data','function-missing']
    rows=[]
    for mode in cases:
        f=P/'fixtures'/('missing-data-'+str(time.time_ns())+'.bbprobe');f.write_bytes(fixture(mode))
        state=P/'state'/('missing-data-runtime-'+str(time.time_ns()))
        q=subprocess.run([str(binary),'--authored-fixture','--boot',str(f),'--state',str(state)],env=env,capture_output=True,text=True,timeout=30)
        text=q.stdout+q.stderr;(E/(mode+'.log')).write_text(text)
        expected=0 if a.expect_old_readable or mode in ['unused','valid-data']else 2 if mode=='execute'else 80
        assert q.returncode==expected,(mode,q.returncode,text[-1600:])
        if mode not in ['valid-data','function-missing']and not a.expect_old_readable:
            assert 'protected-data'in text
            if mode not in ['unused','execute']:assert 'ARM UNIMPLEMENTED DATA IMPORT:'in text
            if mode=='execute':assert 'NoExec'in text
        assert 'implemented in both'not in text
        rows.append(dict(mode=mode,status=q.returncode,expected=expected))
    report=dict(passed=True,expected_old_readable=a.expect_old_readable,binary_sha256=hashlib.file_digest(binary.open('rb'),'sha256').hexdigest(),rows=rows,game_started=False,formal_data_modified=False)
    (E/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(E);print(json.dumps(report,indent=2))
if __name__=='__main__':main()
