"""Actual C++ wire writer/reader and Python preparation decoder agreement."""
from pathlib import Path
import json,re,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from decode_shader_cache import semantic_profile

class UpstreamVertexCacheWire(unittest.TestCase):
 def test_actual_warmup_profile_branch_preserves_legacy_padding(self):
  # Compile the production WarmUp profile decision prefix, not a duplicate
  # decision implementation. Storage is a pure in-memory call-count stub.
  source=(ROOT/'vendor/metal_api/shadps4/video_core/pipeline_cache_core_serialization.cpp').read_text()
  start=source.index('void PipelineCacheCore::WarmUp() {')
  end=source.index('    u32 num_pipelines{};',start)
  prefix=source[start:end]+'    reused=true;\n}\n'
  fixture=r'''
#include "shader_recompiler/profile_cache_wire.h"
#include <string>
#include <cassert>
struct Settings {bool IsPipelineCacheEnabled()const{return true;}} EmulatorSettings;
#define LOG_WARNING(...) ((void)0)
namespace Serialization {constexpr u32 ShaderBinaryVersion=10,ShaderMetaVersion=7,PipelineKeyVersion=5;}
namespace Storage {enum class BlobType {ShaderProfile};struct DataBase {
 std::vector<u8>blob;unsigned clears{},saves{};
 static DataBase& Instance(){static DataBase d;return d;}
 void SetCacheTag(std::string_view){} void Open(){} void FinishPreload(){}
 void Load(BlobType,const char*,std::vector<u8>&out){out=blob;}
 void Clear(){++clears;blob.clear();}
 void Save(BlobType,const char*,std::vector<u8>&&out){++saves;blob=std::move(out);}
};}
namespace VideoCore {struct PipelineCacheCore {Shader::Profile profile{};std::string cache_tag="authored";bool reused{};void WarmUp();};
'''+prefix+r'''
}
int main(){
 constexpr std::array<u32,4>v{0x42425043,10,7,5};
 Shader::Profile p{};p.max_viewport_width=16384;p.max_viewport_height=16384;p.two_level_bda=true;p.relaxed_fragment_compute_float=true;
 auto old=Shader::ProfileCacheWire::Encode(p,v);auto&db=Storage::DataBase::Instance();
 for(unsigned padding=0;padding<256;++padding){auto bytes=old;for(size_t at=74;at<80;++at)bytes[16+at]=padding;
  db={};db.blob=bytes;VideoCore::PipelineCacheCore core;core.profile=p;core.WarmUp();
  assert(core.reused&&db.clears==0&&db.saves==0&&db.blob==bytes);}
 auto current=p;current.relaxed_vertex_float=true;auto blob=Shader::ProfileCacheWire::Encode(current,v);
 db={};db.blob=blob;VideoCore::PipelineCacheCore on;on.profile=current;on.WarmUp();assert(on.reused&&db.clears==0&&db.saves==0);
 db={};db.blob=blob;VideoCore::PipelineCacheCore off;off.profile=p;off.WarmUp();assert(!off.reused&&db.clears==1); // Fake storage only.
}
'''
  with tempfile.TemporaryDirectory(prefix='upstream-warmup-wire-')as td:
   td=Path(td);cpp=td/'warmup.cpp';cpp.write_text(fixture);binary=td/'probe'
   cmd=['/usr/bin/clang++','-std=c++20','-I',str(ROOT/'vendor/metal_api/shadps4'),str(cpp),'-o',str(binary)]
   built=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
   self.assertEqual(built.returncode,0,built.stderr)
   run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
   self.assertEqual(run.returncode,0,run.stdout+run.stderr)

 def test_actual_wire_and_preparation_projection(self):
  with tempfile.TemporaryDirectory(prefix='upstream-profile-wire-')as td:
   td=Path(td);binary=td/'probe';include=ROOT/'vendor/metal_api/shadps4'
   cmd=['/usr/bin/clang++','-std=c++20','-I',str(include),'-I',str(ROOT/'src'),
        str(ROOT/'tests/test_upstream_vertex_cache_wire.cpp'),'-o',str(binary)]
   build=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
   self.assertEqual(build.returncode,0,build.stderr)
   run=subprocess.run([str(binary),str(td)],capture_output=True,text=True,timeout=10)
   self.assertEqual(run.returncode,0,run.stdout+run.stderr)
   layout=json.loads(run.stdout)
   declarations=re.findall(r'^\s*(u32|u64|bool)\s+(\w+)\s*\{',(include/'shader_recompiler/profile.h').read_text(),re.M)
   layout['profile_fields']=[dict(type=t,name=n)for t,n in declarations]
   read=lambda name:semantic_profile((td/name).read_bytes(),layout)
   legacy=read('legacy.bin');padded=read('legacy-padding-one.bin')
   self.assertEqual(legacy['semantic_sha256'],padded['semantic_sha256'])
   self.assertEqual(padded['fields']['relaxed_vertex_float'],0)
   self.assertIn(90,padded['ignored_padding_offsets'])
   current=read('invariant.bin')
   self.assertEqual(current['fields']['relaxed_vertex_float'],1)
   self.assertEqual(current['fields']['max_viewport_width'],16384)
   expected=dict(legacy['fields'],relaxed_vertex_float=1)
   self.assertEqual(current['fields'],expected)
   self.assertNotEqual(current['semantic_sha256'],legacy['semantic_sha256'])
   short=read('legacy-short.bin')
   self.assertEqual(short['fields']['relaxed_fragment_compute_float'],0)
   self.assertEqual(short['fields']['two_level_bda'],0)
   for name in('stripped-feature.bin',):
    with self.assertRaises(Exception):read(name)
   blob=(td/'invariant.bin').read_bytes()
   for length in range(len(blob)):
    with self.subTest(length=length):
     with self.assertRaises(Exception):semantic_profile(blob[:length],layout)
   for at in(20,24,28,36,36+74):
    bad=bytearray(blob);bad[at]^=0x40
    with self.assertRaises(Exception):semantic_profile(bad,layout)

if __name__=='__main__':unittest.main()
