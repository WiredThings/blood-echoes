"""Native CPU lifecycle controls from actual production headers/implementation.

The actual Metal library supplies Image/format symbols but these cases use the
existing undefined-format fixture, issue no GPU commands and open no game.
The separate mtl-scene-targets-test pending-use probe is NOT run here.
"""
from pathlib import Path
import argparse,hashlib,json,os,shlex,subprocess,sys,tempfile

SOURCE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE/'tools'))
from launcher_runtime_profile import capability_environment
PROJECT=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')

def method(source):
    start=source.index('u64 SceneTargetsCore::CollectUnavailable()');body=source.index('{',start);depth=1;end=body+1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}');end+=1
    return start,end

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--compile-commands',type=Path,required=True);parser.add_argument('--library',type=Path,required=True);args=parser.parse_args()
    library=args.library.resolve(strict=True);database=json.loads(args.compile_commands.read_bytes())
    suffix='/vendor/metal_api/shadps4/video_core/scene_targets_core.cpp';entry=next(row for row in database if row['file'].endswith(suffix));old=entry['file'][:-len(suffix)]
    base=list(entry['arguments'])if'arguments'in entry else shlex.split(entry['command']);base=[part.replace(old,str(SOURCE))for part in base]
    base.remove('-c');base+=['-UNDEBUG','-DBB_SCENE_PROXY_NEGATIVE_CONTROL',str(SOURCE/'vendor/tests/test_scene_targets_core.cpp'),str(SOURCE/'vendor/tests/test_gpu_runtime_stubs.cpp'),str(library),'-Wl,-export_dynamic','-Wl,-rpath,'+str(library.parent)]
    text=(SOURCE/'vendor/metal_api/shadps4/video_core/scene_targets_core.cpp').read_text();lo,hi=method(text);body=text[lo:hi]
    variants={'good':text,'old-retention':text[:lo]+'u64 SceneTargetsCore::CollectUnavailable() { return 0; }'+text[hi:]}
    for name,before,after in(
        ('evict-live','if (original && original->image_uid == entry.uid)','if (original && false)'),
        ('slot-only','if (original && original->image_uid == entry.uid)','if (original)'),
        ('stale-tracked','        tracked.erase(entry.uid);',''),
        ('always-generation','    if (removed) ++generation;','    ++generation;')):
        assert before in body;variants[name]=text[:lo]+body.replace(before,after,1)+text[hi:]
    output=Path(tempfile.mkdtemp(prefix='blood-echoes-proxy-0089-',dir='/private/tmp'));rows=[]
    for name,contents in variants.items():
        source=output/(name+'.cpp');source.write_text(contents);target=output/name;command=base.copy();command[command.index('-o')+1]=str(target)
        if'-MF'in command:command[command.index('-MF')+1]=str(target)+'.d'
        command[command.index(str(SOURCE/'vendor/metal_api/shadps4/video_core/scene_targets_core.cpp'))]=str(source)
        (output/(name+'-command.json')).write_text(json.dumps(command,indent=2)+'\n')
        compiled=subprocess.run(command,cwd=entry['directory'],capture_output=True,text=True,timeout=90);(output/(name+'-compile.log')).write_text(compiled.stdout+compiled.stderr);assert compiled.returncode==0,(name,output)
        with capability_environment(PROJECT,qa_root=PROJECT/'state',prefix='proxy-retirement-core-')as environment:
            result=subprocess.run([str(target)],capture_output=True,text=True,env={**environment,'DYLD_PRINT_LIBRARIES':'1'},timeout=30)
        (output/(name+'.log')).write_text(result.stdout+result.stderr)
        loaded=str(library)in result.stderr;want=0 if name=='good'else 7;rows.append(dict(case=name,code=result.returncode,expected=want,actual_library_seen=loaded));assert result.returncode==want and loaded,(name,output,(result.stdout+result.stderr)[-2500:])
    record=dict(status='passed',cases=rows,source_sha256=hashlib.sha256(text.encode()).hexdigest(),library=str(library),library_sha256=hashlib.sha256(library.read_bytes()).hexdigest(),game_started=False,GPU_commands=False,performance_evidence=False,formal_data_modified=False)
    (output/'result.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(dict(output=str(output),**record)))

if __name__=='__main__':main()
