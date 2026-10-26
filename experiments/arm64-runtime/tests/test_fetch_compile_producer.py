"""Authored receipt and actual GetSharp observation, without game/GPU/renderer."""
from pathlib import Path
import copy,hashlib,json,os,struct,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import prepare_fetch_capture_receipt as producer
from game_shader_catalog import game_identity
from test_game_shader_catalog import sfo

CODE=struct.pack('<2I',0xBEEB03FF,0xBF810000);CODE_SHA=hashlib.sha256(CODE).hexdigest()
def fixture(root,backend):
 game=root/'game';(game/'sce_sys').mkdir(parents=True);(game/'sce_sys/param.sfo').write_bytes(sfo())
 catalog=root/'catalog.json';catalog.write_text(json.dumps(dict(game_identity=game_identity(sfo()),packages=[dict(kind='shader',debug=False,entries=[dict(binary_info=[dict(code_prefix_verified=True,program_hash='0000000000001234',code_sha256=CODE_SHA,binary_type_raw=1,code_bytes=8)])])])) )
 index=root/'index.jsonl';index.write_text(json.dumps(dict(schema=1,complete_pso=False,identity=dict(program_hash='0000000000001234',code_sha256=CODE_SHA,hardware_stage='vertex',hardware_stage_value=1),variants=[dict(code_bytes=8,facts=dict(file_stage=1,binary_type=1))]))+'\n')
 return dict(game_root=game,catalog=catalog,index=index,backend=backend)

class ReceiptControls(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='fetch-source-authored-',dir='/private/tmp');self.root=Path(self.temp.name).resolve();self.backend=self.root/'authored-backend';self.backend.write_bytes(b'authored backend bytes');self.args=fixture(self.root,self.backend)
 def tearDown(self):self.temp.cleanup()
 def test_receipt_complete_fixed_sources_and_stage_proof(self):
  data,proof=producer.receipt(**self.args);self.assertEqual(len(data),208);self.assertEqual(data[:8],b'BEFPROD1');self.assertEqual(struct.unpack_from('<IQ32sI',data,160),(1,0x1234,bytes.fromhex(CODE_SHA),8));self.assertEqual(proof['programs'],1)
 def test_packaged_explicit_layout_source_is_used(self):
  source=self.root/'layout-source';(source/'shader_recompiler').mkdir(parents=True);header=ROOT/'vendor/metal_api/shadps4/shader_recompiler/runtime_info.h';(source/'shader_recompiler/runtime_info.h').write_bytes(header.read_bytes());data,proof=producer.receipt(**self.args,runtime_source_root=source);self.assertEqual(len(data),208);self.assertEqual(proof['runtime_source_root'],str(source));(source/'shader_recompiler/runtime_info.h').unlink()
  with self.assertRaises((ValueError,FileNotFoundError)):producer.receipt(**self.args,runtime_source_root=source)
 def test_missing_static_code_and_wrong_native_enum_rejected(self):
  p=self.args['index'];r=json.loads(p.read_text());r['identity']['hardware_stage_value']=4;p.write_text(json.dumps(r)+'\n')
  with self.assertRaisesRegex(ValueError,'native stage'):producer.receipt(**self.args)
  r['identity']['hardware_stage_value']=1;r['identity']['code_sha256']='f'*64;p.write_text(json.dumps(r)+'\n')
  with self.assertRaisesRegex(ValueError,'independent catalog'):producer.receipt(**self.args)
 def test_binary_info_enum_not_treated_as_native_enum(self):
  p=self.args['index'];r=json.loads(p.read_text());r['variants'][0]['facts']['binary_type']=4;p.write_text(json.dumps(r)+'\n')
  with self.assertRaisesRegex(ValueError,'BinaryInfo'):producer.receipt(**self.args)
 def test_wrong_SFO_game_catalog_identity_refused(self):
  (self.args['game_root']/'sce_sys/param.sfo').write_bytes(sfo('01.00'))
  with self.assertRaises(ValueError):producer.receipt(**self.args)
 def test_oversized_static_code_refused_before_receipt_publication(self):
  p=self.args['index'];r=json.loads(p.read_text());r['variants'][0]['code_bytes']=512*1024+4;p.write_text(json.dumps(r)+'\n')
  with self.assertRaisesRegex(ValueError,'owned code byte budget'):producer.receipt(**self.args)
 def test_publish_only_private_root_and_preserve_symlink_target(self):
  data,_=producer.receipt(**self.args);qa=self.root/'qa';qa.mkdir(mode=0o700);target=producer.publish(qa,data);self.assertEqual(target.read_bytes(),data);target.unlink();outside=self.root/'outside';outside.write_bytes(b'preserve authored outside');target.symlink_to(outside)
  with self.assertRaises(ValueError):producer.publish(qa,data)
  self.assertEqual(outside.read_bytes(),b'preserve authored outside');os.chmod(qa,0o755)
  with self.assertRaises(ValueError):producer.publish(qa,data)
 def test_launcher_configuration_returns_existing_exec_environment(self):
  qa=self.root/'cohort';qa.mkdir(mode=0o700);configured=producer.configure_capture(root=qa,**self.args);self.assertEqual(configured['capture_environment'],{'BB_FETCH_CAPTURE_ROOT':str(qa),'BB_FETCH_CAPTURE_GAME_ROOT':str(self.args['game_root'])});self.assertEqual(configured['status'],'configured');self.assertFalse(configured['game_started'])

class RealProducer(unittest.TestCase):
 def test_native_original_read_to_background_atomic_writer(self):
  with tempfile.TemporaryDirectory(prefix='fetch-producer-native-',dir='/private/tmp')as name:
   root=Path(name).resolve();binary=root/'test';qa=root/'qa';qa.mkdir(mode=0o700)
   sources=['tests/test_fetch_compile_producer.cpp','src/fetch_compile_observer.cpp','src/fetch_compile_producer.cpp','src/fetch_compile_canonical.cpp','src/fetch_companion_store.cpp','src/fetch_register_store.cpp']
   includes=[ROOT/'src',ROOT/'vendor/metal_api/shim',ROOT/'vendor/metal_api/shadps4',ROOT/'vendor/metal_api/third_party/half/include',Path('/opt/homebrew/include')]
   command=['/usr/bin/clang++','-std=gnu++23','-O1','-DBB_DEV_FETCH_CAPTURE=1','-DBB_FETCH_CAPTURE_TESTING=1','-Wno-deprecated-declarations',*[f'-I{x}'for x in includes],*[str(ROOT/x)for x in sources],'-o',str(binary)]
   compiled=subprocess.run(command,text=True,capture_output=True,timeout=90);self.assertEqual(compiled.returncode,0,compiled.stderr[-10000:])
   args=fixture(root,binary);data,_=producer.receipt(**args);producer.publish(qa,data)
   run=subprocess.run([str(binary),str(qa),str(args['game_root'])],text=True,capture_output=True,timeout=15);self.assertEqual(run.returncode,0,run.stdout+run.stderr)
   records=[json.loads(x.removeprefix('FETCH_PRODUCER_RESULT '))for x in run.stdout.splitlines()if x.startswith('FETCH_PRODUCER_RESULT ')];self.assertEqual(len(records),1);r=records[0];self.assertGreaterEqual(r['passed'],73);self.assertTrue(r['disk_record_written']);self.assertTrue(r['Vsharp_protected_before_finish']);self.assertFalse(r['game_started']);self.assertFalse(r['GPU_started']);print('FETCH_PRODUCER_RESULT '+json.dumps(r,sort_keys=True))
 def test_compile_gate_and_no_repeated_live_reads_in_observer(self):
  cmake=(ROOT/'vendor/metal_api/CMakeLists.txt').read_text();self.assertIn('option(BB_DEV_FETCH_CAPTURE "Automatic validated local compiler-input capture" ON)',cmake)
  observer=(ROOT/'src/fetch_compile_observer.cpp').read_text();self.assertNotIn('ReadUdReg<',observer);self.assertNotIn('GetSharp(',observer);self.assertNotIn('ParseFetchShader(',observer);self.assertNotIn('CompileSnapshot::Capture(',observer)
  pipeline=(ROOT/'vendor/metal_api/shadps4/video_core/pipeline_cache_core.cpp').read_text();self.assertEqual(pipeline.count('fetch_record.emplace('),1);self.assertEqual(pipeline.count('fetch_record->Finish(spv)'),1)
  compile_body=pipeline.split('ShaderModuleHandle PipelineCacheCore::CompileModule(',1)[1].split('// bbport: BB_SHADER_LOG',1)[0]
  self.assertLess(compile_body.index('fetch_record->Finish(spv)'),compile_body.index('RegisterShaderBinary(std::move(spv)'))
  fetch=(ROOT/'vendor/metal_api/shadps4/shader_recompiler/frontend/fetch_shader.cpp').read_text();self.assertIn('ObserveCapturedFetch(info,it->second.data)',fetch);self.assertIn('ObserveCapturedFetch(info,data)',fetch)
  outputs=pipeline.split('static u32 MapOutputs(',1)[1].split('return num_outputs;',1)[0];self.assertEqual(outputs.count('num_outputs++'),3);self.assertIn('std::span<Shader::OutputMap, 3>',outputs)
 def test_warm_seen_gate_and_permanent_refusal_are_distinct(self):
  pipeline=(ROOT/'vendor/metal_api/shadps4/video_core/pipeline_cache_core.cpp').read_text();self.assertIn('BbFetchCapture::ObserveWarm(',pipeline);self.assertIn('if(capture_state_changed&&program->capture_remaining)',pipeline);self.assertIn('else if(permanent)',pipeline);self.assertIn('!info.translation_failed && BbFetchCapture::Producer::Enabled()',pipeline)
  storage=(ROOT/'vendor/metal_api/shadps4/video_core/cache_storage.cpp').read_text().split('DataBase::BoundedShaderReader(',1)[1].split('void DataBase::ForEachBlob(',1)[0];self.assertIn('if(permanent_rejection)*permanent_rejection=false',storage);self.assertIn('if(!opened)return{};',storage);self.assertNotIn('permanent_rejection=true;\n    if(!opened)',storage)
 def test_current_active_runtime_fields_are_covered(self):
  runtime=(ROOT/'vendor/metal_api/shadps4/shader_recompiler/runtime_info.h').read_text();source=(ROOT/'src/fetch_compile_canonical.cpp').read_text()
  import re
  expected={'SwVertexRuntimeInfo':['step_rate_0','step_rate_1','vertex_sgpr_offset','instance_sgpr_offset','tess_emulated_primitive'],'HwLocalRuntimeInfo':['ls_stride'],'HwExportRuntimeInfo':['vertex_data_size'],'HwVertexRuntimeInfo':['num_outputs','outputs','emulate_depth_negative_one_to_one','clip_disable','user_clip_plane_mask','motion_vectors']}
  for name,fields in expected.items():
   body=re.search(r'struct '+name+r'\s*\{(.*?)bool operator==',runtime,re.S).group(1)
   for field in fields:self.assertIn(field,body);self.assertIn(field,source)
   declarations=re.findall(r'^\s*(?:u32|u16|bool|std::array<OutputMap, 3>)\s+(\w+)',body,re.M);self.assertEqual(declarations,fields)
  props=re.search(r'struct Properties\s*\{(.*?)bool operator==',runtime,re.S).group(1);fields=re.findall(r'^\s*(?:AmdGpu::FpDenormMode|AmdGpu::FpRoundMode|u32)\s+(\w+)',props,re.M);self.assertEqual(fields,['fp_denorm_mode32','fp_denorm_mode16_64','fp_round_mode32','fp_round_mode16_64','num_user_data','num_input_vgprs','num_allocated_vgprs'])
  for field in fields:self.assertIn(field,source)
if __name__=='__main__':unittest.main(verbosity=2)
