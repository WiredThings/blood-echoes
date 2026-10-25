// SPDX-License-Identifier: GPL-2.0-or-later
// Included by the original kernels TU. Factory construction is shared by cold
// callers and admission; replay never invents a second helper implementation.
namespace {
using HK=HostPso::Kind;
const H::Shader* HostRecipeShader(const HostPso::Invocation&i) {
 if(i.kind==HK::Tiling||i.kind==HK::TextureTiling)return &TilingShader(i.bits,i.macro);
 if(i.kind==HK::FaultParser)return &H::FAULT_BUFFER_PROCESS_COMP;
 if(i.kind==HK::UpscaleMerge)return &H::UPSCALE_MERGE_COMP;
 if(i.kind==HK::CameraMotion)return &H::CAMERA_MOTION_COMP;
 return nullptr;
}
std::string HostSource(NSString* text){NSData*d=[text dataUsingEncoding:NSUTF8StringEncoding];return d?std::string(static_cast<const char*>(d.bytes),d.length):std::string{};}
bool HostSeverePressure(){int pressure=0;size_t n=sizeof(pressure);return sysctlbyname("kern.memorystatus_vm_pressure_level",&pressure,&n,nullptr,0)==0&&pressure==4;}
}

id HostKernels::Descriptor(const HostPso::Invocation&i) const {
 if(i.kind==HK::RenderInlineUpdate||i.kind==HK::MsCopy||i.kind==HK::MsDepth||i.kind==HK::PostProcess) {
  auto*d=[MTLRenderPipelineDescriptor new];
  if(i.kind==HK::RenderInlineUpdate)d.rasterizationEnabled=NO;
  if(i.kind==HK::PostProcess){d.label=@"Post process";d.colorAttachments[0].pixelFormat=MTLPixelFormat(i.format);}
  if(i.kind==HK::MsCopy){d.label=[NSString stringWithFormat:@"Image to MS image %u",i.samples];d.rasterSampleCount=i.samples;d.colorAttachments[0].pixelFormat=MTLPixelFormat(i.format);}
  if(i.kind==HK::MsDepth){d.label=[NSString stringWithFormat:@"Color to MS depth %u",i.samples];d.rasterSampleCount=i.samples;d.depthAttachmentPixelFormat=MTLPixelFormat(i.format);}
  return d;
 }
 return [MTLComputePipelineDescriptor new]; // exact convenience-API defaults, not thread limits guessed from dispatch
}
std::optional<HostPso::Recipe> HostKernels::Describe(const HostPso::Invocation&i,id descriptor) const {
 if(!pso_cache||!HostPso::Hex(pso_cache->DiscoveryIdentity()))return {};
 auto profile=LocalLibrary::Options(CompileOptions());if(!profile)return {};
 HostPso::Recipe r;r.invocation=i;r.preparation=pso_cache->DiscoveryIdentity();r.descriptor=PsoCache::ArtifactDescriptorIdentity((__bridge void*)descriptor,[descriptor isKindOfClass:[MTLComputePipelineDescriptor class]]);
 std::string sources;
 const auto source=[&](std::string_view text,std::string_view entry){for(auto part:{text,entry}){sources+=std::to_string(part.size());sources+=':';sources+=part;}};
 const auto shader=HostRecipeShader(i);
 if(shader){
  r.entry0=std::string(shader->entry);
  if(i.kind==HK::TextureTiling){const auto text=TextureTilingSource(shader->msl,i.bits==64);if(text.empty())return {};source(text,"main0");r.entry0="main0";}
  else source(shader->msl,shader->entry);
  for(const auto&spec:shader->spec_constants){
   if(spec.id>=HostPso::MaxConstants)return {};
   auto type=HostPso::Type(std::uint32_t(spec.type)+1);
   const bool explicit_value=spec.id<i.count;
   if(i.kind==HK::TextureTiling&&explicit_value&&type!=HostPso::Type::Bool)type=HostPso::Type::U32; // original TextureTiling API uses UInt
   const auto bits=explicit_value?i.provided[spec.id]:spec.default_value;
   r.constants.push_back({spec.id,type,HostPso::Bits(type,bits)});
  }
  if(i.kind==HK::TextureTiling){
   std::erase_if(r.constants,[](const auto&c){return c.id==12||c.id==13;});
   r.constants.push_back({12,HostPso::Type::U32,i.proxy?2u:1u});r.constants.push_back({13,HostPso::Type::Bool,i.bounded});
  }
 }else if(i.kind==HK::InlineUpdate){source(HostSource(InlineUpdateSource),"inline_update");r.entry0="inline_update";}
 else if(i.kind==HK::RenderInlineUpdate){source(HostSource(RenderInlineUpdateSource),"update");r.entry0="update";}
 else if(i.kind==HK::Fill){source(HostSource(FillSource),"fill_u32");r.entry0="fill_u32";}
 else if(i.kind==HK::MsCopy||i.kind==HK::MsDepth||i.kind==HK::PostProcess){
  const H::Shader&fragment=i.kind==HK::PostProcess?H::POST_PROCESS_FRAG:(i.kind==HK::MsDepth?H::COLOR_TO_MS_DEPTH_FRAG:(i.src_msaa?H::MS_IMAGE_BLIT_MSAA_FRAG:H::MS_IMAGE_BLIT_FRAG));
  source(H::FS_TRI_VERT.msl,H::FS_TRI_VERT.entry);source(fragment.msl,fragment.entry);r.entry0=std::string(H::FS_TRI_VERT.entry);r.entry1=std::string(fragment.entry);
 }else return {};
 std::sort(r.constants.begin(),r.constants.end(),[](const auto&a,const auto&b){return a.id<b.id;});
 r.source_options=LocalLibrary::Key(r.preparation,LocalAot::Sha(sources.data(),sources.size()),[profile[@"language"] UTF8String],[profile[@"math_mode"] UTF8String],[profile[@"math_fp32"] UTF8String]);
 return HostPso::Valid(r)?std::optional{std::move(r)}:std::nullopt;
}
void HostKernels::RecordRecipe(const HostPso::Invocation&i,id descriptor) {
 try {
 if(warming||!Storage::DataBase::Instance().IsOpened())return;
 auto recipe=Describe(i,descriptor);if(!recipe)return;
 NSString*first=[descriptor isKindOfClass:[MTLComputePipelineDescriptor class]]?[(MTLComputePipelineDescriptor*)descriptor computeFunction].name:[(MTLRenderPipelineDescriptor*)descriptor vertexFunction].name;
 NSString*second=[descriptor isKindOfClass:[MTLRenderPipelineDescriptor class]]?[(MTLRenderPipelineDescriptor*)descriptor fragmentFunction].name:nil;
 if(!first||recipe->entry0!=std::string(first.UTF8String)||(second?std::string(second.UTF8String):std::string{})!=recipe->entry1)return;
 auto bytes=HostPso::Encode(*recipe);if(bytes.empty())return;
 const auto key=LocalAot::Sha(bytes.data(),bytes.size());
 Storage::DataBase::Instance().Save(Storage::BlobType::HostPipelineRecipe,"h2_"+recipe->preparation+"_"+key,std::move(bytes));
 }catch(...){std::fputs("Metal helper metadata deferred; valid native pipeline retained\n",stderr);}
}
namespace {
bool HostFunctionEntries(const HostPso::Recipe&r,id descriptor){
 NSString*first=[descriptor isKindOfClass:[MTLComputePipelineDescriptor class]]?[(MTLComputePipelineDescriptor*)descriptor computeFunction].name:[(MTLRenderPipelineDescriptor*)descriptor vertexFunction].name;
 NSString*second=[descriptor isKindOfClass:[MTLRenderPipelineDescriptor class]]?[(MTLRenderPipelineDescriptor*)descriptor fragmentFunction].name:nil;
 return first&&r.entry0==std::string(first.UTF8String)&&r.entry1==(second?std::string(second.UTF8String):std::string{});
}
}
id<MTLComputePipelineState> HostKernels::MakeCompute(MTLComputePipelineDescriptor*d,const HostPso::Invocation&i,std::span<const LocalLibrary::Origin> origins,NSError**error) {
 if(!d.computeFunction)return nil;
 std::optional<HostPso::Recipe> recipe;
 try{recipe=Describe(i,d);}catch(...){if(warming){artifact_outcome.state=HostPso::ArtifactState::Failed;return nil;}}
 if(warming&&(!recipe||!expected_recipe||*recipe!=*expected_recipe||!HostFunctionEntries(*recipe,d))){artifact_outcome.state=HostPso::ArtifactState::Failed;return nil;}
 id<MTLComputePipelineState> state=nil;
 try {
 if(pso_cache&&recipe&&HostFunctionEntries(*recipe,d)){
  const auto bytes=HostPso::ArtifactIdentityBytes(*recipe,pso_cache->PreparationIdentity(),origins);
  if(bytes.empty()){
   if(warming){artifact_outcome.state=HostPso::ArtifactState::Failed;return nil;}
  }else{
  const auto key=LocalAot::Sha(bytes.data(),bytes.size());std::string why;
  state=(__bridge_transfer id<MTLComputePipelineState>)pso_cache->CreateArtifactPipeline((__bridge void*)d,true,key,warming,artifact_outcome,why,!warming||warm_allow_compile);
  if(!state&&error)*error=[NSError errorWithDomain:@"bbport-host-pso" code:1 userInfo:@{NSLocalizedDescriptionKey:@(why.c_str())}];
  }
  if(bytes.empty())state=[dev.device newComputePipelineStateWithFunction:d.computeFunction error:error];
 }else state=[dev.device newComputePipelineStateWithFunction:d.computeFunction error:error];
 }catch(...){
  artifact_outcome.state=HostPso::ArtifactState::Failed;
  if(!warming&&!state){d.binaryArchives=@[];state=[dev.device newComputePipelineStateWithFunction:d.computeFunction error:error];}
 }
 if(state)RecordRecipe(i,d);return state;
}
id<MTLRenderPipelineState> HostKernels::MakeRender(MTLRenderPipelineDescriptor*d,const HostPso::Invocation&i,std::span<const LocalLibrary::Origin> origins,NSError**error) {
 if(!d.vertexFunction||((i.kind==HK::MsCopy||i.kind==HK::MsDepth||i.kind==HK::PostProcess)&&!d.fragmentFunction))return nil;
 std::optional<HostPso::Recipe> recipe;
 try{recipe=Describe(i,d);}catch(...){if(warming){artifact_outcome.state=HostPso::ArtifactState::Failed;return nil;}}
 if(warming&&(!recipe||!expected_recipe||*recipe!=*expected_recipe||!HostFunctionEntries(*recipe,d))){artifact_outcome.state=HostPso::ArtifactState::Failed;return nil;}
 id<MTLRenderPipelineState> state=nil;
 try {
 if(pso_cache&&recipe&&HostFunctionEntries(*recipe,d)){
  const auto bytes=HostPso::ArtifactIdentityBytes(*recipe,pso_cache->PreparationIdentity(),origins);
  if(bytes.empty()){
   if(warming){artifact_outcome.state=HostPso::ArtifactState::Failed;return nil;}
  }else{
  const auto key=LocalAot::Sha(bytes.data(),bytes.size());std::string why;
  state=(__bridge_transfer id<MTLRenderPipelineState>)pso_cache->CreateArtifactPipeline((__bridge void*)d,false,key,warming,artifact_outcome,why,!warming||warm_allow_compile);
  if(!state&&error)*error=[NSError errorWithDomain:@"bbport-host-pso" code:1 userInfo:@{NSLocalizedDescriptionKey:@(why.c_str())}];
  }
  if(bytes.empty())state=[dev.device newRenderPipelineStateWithDescriptor:d error:error];
 }else state=[dev.device newRenderPipelineStateWithDescriptor:d error:error];
 }catch(...){
  artifact_outcome.state=HostPso::ArtifactState::Failed;
  if(!warming&&!state){d.binaryArchives=@[];state=[dev.device newRenderPipelineStateWithDescriptor:d error:error];}
 }
 if(state)RecordRecipe(i,d);return state;
}
bool HostKernels::Replay(const HostPso::Recipe&r) {
 const auto&i=r.invocation;id descriptor=Descriptor(i);auto current=Describe(i,descriptor);
 if(!current||*current!=r)return false; // before functions/driver work; stale recipes cannot poison maps
 if(i.kind==HK::Tiling||i.kind==HK::TextureTiling){
  VideoCore::TilingDispatch dispatch{};dispatch.key.num_bits=i.bits;dispatch.is_macro=i.macro;
  const unsigned provided=std::min<unsigned>(i.count,dispatch.constants.size());dispatch.num_constants=provided;std::copy_n(i.provided.begin(),provided,dispatch.constants.begin());
  dispatch.key.is_tiler=i.provided[3]!=0;dispatch.params.range=i.bounded?VideoCore::TilerBounds::ParamsV1Bytes:sizeof(VideoCore::TilingInfo);
  if(i.kind==HK::TextureTiling)return bool(TextureTiling(dispatch,i.proxy));const H::Shader*shader=nullptr;return bool(Tiling(dispatch,shader));
 }
 if(i.kind==HK::FaultParser){
  if(!fault_known||i.count!=2||i.provided[0]!=fault_arguments[0]||i.provided[1]!=fault_arguments[1])return false;
  return CreateFaultParser(i.provided[0],i.provided[1]);
 }
 if(i.kind==HK::InlineUpdate)return bool(InlineUpdate());if(i.kind==HK::RenderInlineUpdate)return bool(RenderInlineUpdate());if(i.kind==HK::Fill)return bool(FillU32());
 if(i.kind==HK::MsCopy)return bool(MsCopyPipeline(i.samples,MTLPixelFormat(i.format),i.src_msaa));
 if(i.kind==HK::MsDepth)return bool(MsDepthPipeline(i.samples,MTLPixelFormat(i.format)));
 if(i.kind==HK::UpscaleMerge){NSError*error=nil;return bool(UpscaleMerge(&error));}
 if(i.kind==HK::CameraMotion){NSError*error=nil;return bool(CameraMotionPipeline(&error));}
 if(i.kind==HK::PostProcess){NSError*error=nil;return bool(PostProcessPipeline(MTLPixelFormat(i.format),&error));}
 return false;
}
void HostKernels::BindPsoCache(PsoCache*cache){
 // Every explicit binding establishes a fresh synchronous preparation generation.
 fault_with_cache=false;expected_recipe=nullptr;artifact_outcome={};warm_result={};
 pso_cache=cache;
}
HostPso::WarmResult HostKernels::WarmPipelines() {
 warm_result={};
 if(!pso_cache||!HostPso::Hex(pso_cache->DiscoveryIdentity())||!Storage::DataBase::Instance().IsOpened())return warm_result;
 if(HostSeverePressure()){warm_result.state=HostPso::WarmState::Critical;return warm_result;}
 struct Item{std::string key;HostPso::Recipe recipe;};std::vector<Item>items;std::vector<std::string>names;
 const auto cursor=pso_cache->HostPreparationCursor();
 // Retain at most 4096 recipes, but scan the bounded catalog rather than always
 // selecting its first filesystem prefix. Nearest keys after cursor come first,
 // then wrap; large catalogs therefore progress without growing resident maps.
 std::map<std::pair<bool,std::string>,HostPso::Recipe> selected;
 const auto prefix="h2_"+pso_cache->DiscoveryIdentity()+"_";
 const auto read=Storage::DataBase::Instance().ForEachBlobBounded(Storage::BlobType::HostPipelineRecipe,prefix,65536,HostPso::MaxBytes,
  [&](std::string_view name,std::vector<u8>&&bytes){
   const auto key=LocalAot::Sha(bytes.data(),bytes.size());
   auto recipe=name==prefix+key+".hostpso"?HostPso::Decode(bytes):std::nullopt;
   if(!recipe||recipe->preparation!=pso_cache->DiscoveryIdentity()){++warm_result.rejected;return true;}
   ++warm_result.known;
   selected.emplace(std::make_pair(key<=cursor,key),std::move(*recipe));
   if(selected.size()>HostPso::MaxMetadata)selected.erase(std::prev(selected.end()));
   return true;
  });
 for(auto&[rank,recipe]:selected){names.push_back(rank.second);items.push_back({rank.second,std::move(recipe)});}
 selected.clear();
 warm_result.catalog_complete=read.complete()&&warm_result.known<=HostPso::MaxMetadata;
 warm_result.rejected+=read.rejected;
 const auto order=HostPso::Order(names,cursor);
 const auto started=std::chrono::steady_clock::now();unsigned attempts=0;std::string after;
 const bool previous_warming=warming;warming=true;
 SCOPE_EXIT{warming=previous_warming;expected_recipe=nullptr;warm_allow_compile=true;};
 for(const auto index:order){
  if(HostSeverePressure()){warm_result.state=HostPso::WarmState::Critical;break;}
  if(std::chrono::steady_clock::now()-started>std::chrono::seconds(10))break;
  const auto&item=items[index];expected_recipe=&item.recipe;artifact_outcome={};warm_allow_compile=attempts<HostPso::MaxWarm;
  bool accepted=false;
  @autoreleasepool {try{accepted=Replay(item.recipe);}catch(...){artifact_outcome.state=HostPso::ArtifactState::Failed;}}
  ++warm_result.processed;after=item.key;if(artifact_outcome.compiled)++attempts;
  if(artifact_outcome.state==HostPso::ArtifactState::Critical){warm_result.state=HostPso::WarmState::Critical;break;}
  if(accepted&&artifact_outcome.ready()){++warm_result.ready;if(artifact_outcome.state==HostPso::ArtifactState::Built)++warm_result.built;}
  else if(artifact_outcome.state==HostPso::ArtifactState::Deferred)++warm_result.deferred;
  else if(artifact_outcome.state==HostPso::ArtifactState::Failed)++warm_result.failed;
  else ++warm_result.rejected; // obsolete/incompatible producer, never an admitted PSO
 }
 warm_result.deferred+=warm_result.known-warm_result.processed;
 if(warm_result.state!=HostPso::WarmState::Critical){
  warm_result.state=(!warm_result.catalog_complete||warm_result.failed||warm_result.deferred)?HostPso::WarmState::Deferred:HostPso::WarmState::Complete;
  try{pso_cache->HostPreparationProgress(warm_result,warm_result.state==HostPso::WarmState::Complete?std::string_view{}:std::string_view(after));}catch(...){}
 }
 std::printf("Metal host PSO preparation: state=%u known=%u processed=%u ready=%u built=%u rejected=%u failed=%u deferred=%u catalog-complete=%d; temporary states, original maps unchanged\n",unsigned(warm_result.state),warm_result.known,warm_result.processed,warm_result.ready,warm_result.built,warm_result.rejected,warm_result.failed,warm_result.deferred,warm_result.catalog_complete);
 return warm_result;
}
HostPso::WarmResult HostKernels::FinishPreparation(){
 if(HostSeverePressure()){warm_result.state=HostPso::WarmState::Critical;return warm_result;}
 // This is the first real fault-parser creation when normal PipelineCache setup
 // deferred it. The no-PsoCache getter below uses the same actual factory.
 if(fault_known&&!fault_parser)CreateFaultParser(fault_arguments[0],fault_arguments[1]);
 return warm_result;
}
void Runtime::BindHostPsoCache(PsoCache*cache){impl->kernels.BindPsoCache(cache);}
HostPso::WarmResult Runtime::WarmHostPipelines(){return impl->kernels.WarmPipelines();}
HostPso::WarmResult Runtime::FinishHostPreparation(){return impl->kernels.FinishPreparation();}
const HostPso::WarmResult&Runtime::HostPreparationResult()const{return impl->kernels.PreparationResult();}
bool Runtime::HostPreparationCritical()const{return HostSeverePressure();}
