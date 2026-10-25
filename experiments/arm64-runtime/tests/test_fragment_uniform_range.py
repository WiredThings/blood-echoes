"""Exact before/after suffix matcher comparison, authored data or private diagnostic cache."""
from pathlib import Path
import argparse,json,subprocess,sys,time
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
ap=argparse.ArgumentParser();ap.add_argument('--cache',type=Path);ap.add_argument('--pixels',action='store_true');a=ap.parse_args();require_no_games()
h=P/'vendor/metal_api/shadps4/video_core/renderer_metal/mtl_fragment_uniforms.h';source=h.read_text()
current="""            // Match the same anchored expression directly against the original suffix.
            // Avoid a full suffix allocation/copy for every read; no match flags change.
            std::match_results<std::string::const_iterator> match;
            if(!std::regex_search(code.cbegin()+j->position(),code.cend(),match,access)) {valid=false;break;}"""
legacy="""            const std::string tail=code.substr(j->position());std::smatch match;
            if(!std::regex_search(tail,match,access)) {valid=false;break;}"""
assert source.count(current)==1
reference=source.replace(current,legacy).replace('namespace Metal {','namespace LegacyMetal {')
e=P/'evidence'/('fragment-uniform-range-controls-'+str(time.time_ns()));e.mkdir();(e/'reference.h').write_text(reference)
exe=e/'range-test';subprocess.run(['/usr/bin/clang++','-std=c++20','-O2','-arch','arm64','-UNDEBUG','-I'+str(h.parent),'-I'+str(e),str(P/'tests/test_fragment_uniform_range.cpp'),'-o',str(exe)],check=True)
files=[]
if a.cache:
 cache=a.cache.absolute();assert cache==cache.resolve() and cache.parent==P/'state' and cache.name.startswith('gpu-diagnostic-') and cache.is_dir()
 files=sorted(cache.rglob('*.metal'));assert all(x.is_file() and not x.is_symlink() and x.stat().st_size<2*1024*1024 for x in files)
run=subprocess.run([str(exe)]+[str(x)for x in files],text=True,capture_output=True,timeout=120);(e/'console.log').write_text(run.stdout+run.stderr);assert run.returncode==0,str(e)
pixel=None
if a.pixels:
 exe=e/'pixel-test'
 subprocess.run(['/usr/bin/clang++','-std=c++20','-O2','-fobjc-arc','-arch','arm64','-UNDEBUG','-I'+str(P/'vendor/metal_api/shadps4'),str(P/'tests/metal/test_fragment_uniforms.mm'),'-framework','Foundation','-framework','Metal','-o',str(exe)],check=True,timeout=45)
 pixel=subprocess.run([str(exe)],text=True,capture_output=True,timeout=30);(e/'pixel-console.log').write_text(pixel.stdout+pixel.stderr);assert pixel.returncode==0,str(e)
(e/'result.json').write_text(json.dumps(dict(passed=True,game_started=False,cache_source=str(a.cache)if a.cache else None,source_byte_identical=True,corpus_files=len(files),console=run.stdout,pixel_cases=24 if pixel else None,pixel_console=pixel.stdout if pixel else None,qualification='Host source-processing microbenchmark only; not game FPS or full-process memory evidence.'),indent=2)+'\n');print(e);print(run.stdout)
if pixel:print(pixel.stdout)
