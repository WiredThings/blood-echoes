"""Actual Metal textures -> existing readback -> bounded, format-proven RGB preview."""
from pathlib import Path
import hashlib,json,subprocess,sys,time
from PIL import Image
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
from decode_present_image import decode,from_log
require_no_games();e=P/'evidence'/('present-dump-controls-'+str(time.time_ns()));e.mkdir();images=e/'images';images.mkdir(mode=0o700)
exe=e/'present-test';cmd=['/usr/bin/clang++','-std=c++20','-arch','arm64','-fobjc-arc','-UNDEBUG','-I'+str(P/'vendor/metal_api/shadps4'),'-I'+str(P/'vendor/aether/runtime/sources/fexcore-darwin/External/fmt/include'),str(P/'tests/metal/test_present_dump.mm'),'-framework','Metal','-framework','Foundation','-o',str(exe)]
with(e/'build.log').open('w')as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
r=subprocess.run([str(exe),str(images)],capture_output=True,text=True,timeout=30);text=r.stdout+r.stderr;(e/'console.log').write_text(text);assert r.returncode==0,text
rows=from_log(images,text);assert len(rows)==18
for row in rows:
 assert row['width']==65 and row['height']==17
 with Image.open(row['png'])as im:
  assert im.mode=='RGB'and im.size==(65,17)
  for y in range(17):
   for x in range(65):
    color=((255,0,0)if x<32 else(0,255,0))if y<9 else((0,0,255)if x<32 else(128,128,128))
    if x>=32 and y>=9 and int(Path(row['raw']).name[1:4])%3 and row['metal_format']in(71,81):color=(188,188,188)
    assert im.getpixel((x,y))==color,(row['metal_format'],x,y,im.getpixel((x,y)))
# Required native format metadata, tight-size and ownership checks are fail closed.
try:from_log(images,text+text)
except ValueError:pass
else:raise AssertionError('duplicate format proof accepted')
try:decode(Path(rows[0]['raw']),80)
except ValueError:pass
else:raise AssertionError('layout mismatch accepted')
bad=images/'f099_present_65x17_rgba.raw';bad.write_bytes(b'x')
try:decode(bad,70)
except ValueError:pass
else:raise AssertionError('partial file accepted')
alias=images/'f098_present_65x17_rgba.raw';alias.symlink_to(Path(rows[0]['raw']))
try:decode(alias,70)
except ValueError:pass
else:raise AssertionError('symlink accepted')
assert not list(images.glob('f018_*.raw'))and not list(images.glob('f019_*.raw'))
result=dict(passed=True,actual_metal=True,formats=rows,unsupported_and_failed_allocation_rejected=True,game_started=False,formal_data_modified=False)
(e/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(e);print('PASS six actual Metal formats, transfer plus independent render colors/sRGB, orientation, odd rows and rejects')
