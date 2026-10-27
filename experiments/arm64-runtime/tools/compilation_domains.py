"""Compile identity vs launch provenance. Digests are NOT artifact admission.

This is shared by the existing manifest/builder paths; legacy native keys remain
intact until retained-artifact migration and native adoption are integrated.
Unknown options remain in compatibility, explicitly marked for field audit.
"""
import hashlib,json,re

HEX=re.compile(r'[0-9a-f]{64}')
# The native worker/DSO, ABI, actual input manifest, concrete compiler options,
# local toolchain/environment and artifact bytes are separate Schedule fields.
# These are the Python input/admission/scheduling protocols for that proof.
# Whole-bundle admission still checks EVERY frozen tool before invocation.
PSO_SCHEDULE_PROTOCOLS=frozenset(('prepare_game_psos.py','game_pso_queue.py',
 'game_pso_progress_identity.py','game_pso_owned_audit.py','decode_shader_cache.py',
 'pso_startup_schedule.py','startup_change_journal.py','incremental_file_hashes.py',
 'launcher_game_catalog.py'))

def pso_schedule_identity(admitted_tools):
 if not isinstance(admitted_tools,dict)or not PSO_SCHEDULE_PROTOCOLS<=set(admitted_tools):raise ValueError('complete admitted PSO protocols required')
 selected={k:admitted_tools[k]for k in sorted(PSO_SCHEDULE_PROTOCOLS)}
 if any(not isinstance(v,str)or not HEX.fullmatch(v)for v in selected.values()):raise ValueError('actual admitted PSO protocol bytes required')
 return dict(schema=1,kind='pso-scheduling-protocols',files=selected)

# Audited execution-only consumers: mtl_scheduler.mm, mtl_render_list.mm,
# mtl_runtime.mm. These affect residency/fences/staging, not source generation.
EXECUTION_OPTIONS=frozenset(('BB_MTL_PRECISE_RESIDENCY','BB_MTL_COALESCE_BARRIERS',
 'BB_MTL_HOT_STAGING_REUSE','BB_MTL_GPU_TIMING'))
# Actual source/function compilation controls read by mtl_shader.mm and
# PsoCache's native compatibility options. Keep concrete values, including unset
# vs explicitly supplied values; unknown future controls are never dropped.
COMPILE_OPTIONS=frozenset(('BB_MTL_STRICT_FLOAT','BB_MTL_RELAXED_MATH',
 'BB_MTL_VERTEX_MATH','BB_MTL_FRAGMENT_UNIFORMS','BB_MTL_COPY_PAIR','BB_GS_DEBUG',
 'MTL_SHADER_VALIDATION','MTL_SHADER_VALIDATION_ENABLE_PIPELINES',
 'MTL_SHADER_VALIDATION_DISABLE_PIPELINES','MTL_DEBUG_LAYER'))

def digest(value):
 return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()

def split_manifest(manifest):
 if not isinstance(manifest,dict)or set(manifest)!={'schema','translator','game','options','profile'}or type(manifest['schema'])is not int or manifest['schema']!=1:raise ValueError('complete legacy manifest required')
 if not manifest['translator']or not manifest['game']or not manifest['profile']:raise ValueError('missing witnessed manifest identity')
 for group in('translator','game'):
  for row in manifest[group].values():
   if set(row)!={'sha256','bytes'}or not isinstance(row['sha256'],str)or not HEX.fullmatch(row['sha256'])or type(row['bytes'])is not int or row['bytes']<=0:raise ValueError('actual manifest byte identity required')
 options=manifest['options']
 if not isinstance(options,dict)or any(not isinstance(k,str)or not isinstance(v,str)for k,v in options.items()):raise ValueError('concrete option strings required')
 compiler=dict(schema=2,kind='gpu-compilation-compatibility',translator=manifest['translator'],
  profile=manifest['profile'],options={k:v for k,v in options.items()if k not in EXECUTION_OPTIONS})
 selection=dict(schema=1,kind='launch-selection-provenance',game=manifest['game'],
  execution_options={k:v for k,v in options.items()if k in EXECUTION_OPTIONS})
 return dict(compilation_token=digest(compiler),compilation_manifest=compiler,
  selection_token=digest(selection),selection_manifest=selection,
  unclassified_options=sorted(set(options)-EXECUTION_OPTIONS-COMPILE_OPTIONS),artifact_authority=False)

def library_product_key(request,build_id):
 """Full real MSL/options/build identity, separate from original cohort/key.

 Preserve native v1 entry semantics and v2 full-library semantics. Verify the
 ORIGINAL native key first; this portable product key never authorizes import.
 """
 from local_metal_builder import REQUEST_FIELDS,LIBRARY_REQUEST_FIELDS,derive_key,RECIPE,LIBRARY_RECIPE
 schema=request.get('schema');expected=REQUEST_FIELDS if type(schema)is int and schema==1 else LIBRARY_REQUEST_FIELDS if type(schema)is int and schema==2 else None
 if expected is None or set(request)!=expected:raise ValueError('complete supported native library request required')
 if any(not isinstance(v,str)for k,v in request.items()if k!='schema'):raise ValueError('concrete native request values required')
 if request['recipe']!=(RECIPE if schema==1 else LIBRARY_RECIPE):raise ValueError('actual native recipe required')
 for value in(request['cohort'],request['source_sha256'],build_id):
  if not isinstance(value,str)or not HEX.fullmatch(value):raise ValueError('bounded compilation identity required')
 if derive_key(request)!=request['key']:raise ValueError('original full native key mismatch')
 # Cohort, key and locations remain provenance. All other native request
 # fields are retained; do not invent equivalence for source or options.
 inputs={k:v for k,v in request.items()if k not in('cohort','key')}
 return digest(dict(schema=1,kind='metal-library-product',inputs=inputs,toolchain_build_id=build_id))
