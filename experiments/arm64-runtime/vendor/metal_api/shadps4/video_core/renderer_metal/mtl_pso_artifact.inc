// SPDX-License-Identifier: GPL-2.0-or-later
// One immutable native artifact per complete producer recipe. No state map,
// main-archive mutation, loaded-archive append or permanent archive residency.
namespace {
struct ArtifactArchive {
 id<MTLBinaryArchive> archive=nil;
 bool persist_enabled=true,archive_dirty{},archive_error_logged{};
 std::mutex archive_mutex;
 std::vector<id> archive_descriptors;
};
bool ArtifactCritical(){int level=0;size_t size=sizeof(level);return sysctlbyname("kern.memorystatus_vm_pressure_level",&level,&size,nullptr,0)==0&&level==4;}
bool ArtifactReplace(const std::filesystem::path&path,NSData*bytes){
 if(!bytes)return false;static std::atomic<u64> sequence{};
 const auto temp=path.string()+".tmp-"+std::to_string(getpid())+"-"+std::to_string(sequence++);
 const int fd=open(temp.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);if(fd<0)return false;
 bool ok=true;size_t at=0;
 while(at<bytes.length){const auto n=write(fd,static_cast<const char*>(bytes.bytes)+at,bytes.length-at);if(n<0&&errno==EINTR)continue;if(n<=0){ok=false;break;}at+=size_t(n);}
 if(ok)ok=fsync(fd)==0;if(close(fd)!=0)ok=false;
 if(ok)ok=rename(temp.c_str(),path.c_str())==0; // complete candidate only; old pointer survives failure
 unlink(temp.c_str());return ok;
}
std::optional<std::filesystem::path> ArtifactRoot(const std::filesystem::path&cache,std::string_view preparation,bool create){
 if(!LocalAot::Hex(preparation)||!LocalAot::Directory(cache,false))return {};
 const auto root=cache/("native-pipeline-artifacts-v1-"+std::string(preparation));
 if(!LocalAot::Directory(root,create))return {};return root;
}
std::optional<std::filesystem::path> ArtifactInput(const std::filesystem::path&root,std::string_view key,std::string_view preparation,std::string*verified_sha=nullptr){
 const auto receipt=LocalAot::Json(root/(std::string(key)+".json"));
 if(!receipt||!LocalAot::Integer(receipt,@"schema",1)||!LocalAot::String(receipt,@"recipe_key",key)||!LocalAot::String(receipt,@"preparation",preparation))return {};
 id value=receipt[@"archive"];if(![value isKindOfClass:[NSString class]])return {};
 const std::string name=[value UTF8String];
 if(name.size()>240||!name.starts_with(std::string(key)+"-")||!name.ends_with(".binaryarchive")||name.find('/')!=name.npos||name.find("..")!=name.npos||name.find('\\')!=name.npos)return {};
 auto bytes=LocalAot::Read(root/name,LocalAot::MaxLibrary);
 if(!bytes||!LocalAot::Integer(receipt,@"archive_bytes",bytes.length))return {};
 const auto digest=LocalAot::Sha(bytes.bytes,bytes.length);
 if(!LocalAot::String(receipt,@"archive_sha256",digest))return {};
 if(verified_sha)*verified_sha=digest;
 return root/name;
 }
void RememberPreparedArtifact(const std::filesystem::path&root,std::string_view key,std::string_view preparation)noexcept{
 try {
 // A small scheduling index, never an admission receipt. Readers revalidate the
 // referenced receipt and bytes; actual factories still choose their own key.
 NSDictionary*v=@{@"schema":@1,@"preparation":@(std::string(preparation).c_str()),@"key":@(std::string(key).c_str())};
 ArtifactReplace(root/"startup-entry.json",[NSJSONSerialization dataWithJSONObject:v options:NSJSONWritingSortedKeys error:nil]);
 }catch(...){ } // Optional index failure never changes a valid artifact outcome.
}
bool HasPreparedArtifact(const std::filesystem::path&cache,std::string_view preparation){
 try {
  auto root=ArtifactRoot(cache,preparation,false);if(!root)return false;
  auto entry=LocalAot::Json(*root/"startup-entry.json");
  if(!entry||!LocalAot::Integer(entry,@"schema",1)||!LocalAot::String(entry,@"preparation",preparation))return false;
  id value=entry[@"key"];if(![value isKindOfClass:[NSString class]])return false;
  const std::string key=[value UTF8String];return LocalAot::Hex(key)&&ArtifactInput(*root,key,preparation).has_value();
 }catch(...){return false;} // Optional preference cannot invalidate original startup.
}
}

template<class Descriptor,class Create,class Add>
id PsoArtifactCreate(id<MTLDevice> device,Descriptor*descriptor,const std::filesystem::path&cache,
 std::string_view preparation,std::string_view key,bool prepare,bool allow_compile,bool main_live,
 HostPso::ArtifactOutcome&out,std::string&error,Create create,Add add){
 using S=HostPso::ArtifactState;out.state=S::Unavailable;
 if(prepare&&ArtifactCritical()){out.state=S::Critical;return nil;}
 if(!LocalAot::Hex(preparation)||!LocalAot::Hex(key)||main_live){
  if(prepare||!allow_compile){out.state=S::Deferred;error=main_live?"main archive explicitly retained":"unverified artifact identity";return nil;}
  NSError*e=nil;out.compiled=true;id state=create(MTLPipelineOptionNone,&e);if(!state)error=FirstLine(e);return state;
 }
 auto root=ArtifactRoot(cache,preparation,prepare);
 ArtifactArchive input;bool loaded=false;std::string input_sha;
 if(root)if(auto path=ArtifactInput(*root,key,preparation,&input_sha)){
  auto*d=[MTLBinaryArchiveDescriptor new];d.url=[NSURL fileURLWithPath:@(path->c_str())];
  input.archive=[device newBinaryArchiveWithDescriptor:d error:nil];loaded=input.archive!=nil;
 }
 bool hit=false;
 id state=CreateWithArchive(input,loaded,descriptor,create,add,hit,error,allow_compile,&out.compiled);
 // CreateWithArchive never appends a loaded object in this persist/read-only
 // context. Release it and all retained descriptors before a fresh candidate.
 descriptor.binaryArchives=@[];input.archive=nil;input.archive_descriptors.clear();
 if(hit){if(prepare&&root)RememberPreparedArtifact(*root,key,preparation);out.state=S::Hit;out.archive_sha256=std::move(input_sha);return state;}
 if(!state){out.state=allow_compile?S::Failed:S::Deferred;return nil;}
 if(!prepare){out.state=S::Unavailable;return state;} // no serialization in gameplay
 if(ArtifactCritical()){out.state=S::Critical;return nil;}
 if(!root){out.state=S::Failed;error="artifact root unavailable";return state;}
 id<MTLBinaryArchive> fresh=[device newBinaryArchiveWithDescriptor:[MTLBinaryArchiveDescriptor new] error:nil];
 NSError*native_error=nil;
 if(!fresh||!add(fresh,descriptor,&native_error)){out.state=S::Failed;error=FirstLine(native_error);return state;}
 // Fresh is serialized exactly once; a loaded success is never repacked.
 static std::atomic<u64> sequence{};const auto suffix=std::to_string(getpid())+"-"+std::to_string(sequence++);
 const auto temp=*root/(std::string(key)+".candidate-"+suffix);
 bool keep=false;SCOPE_EXIT{if(!keep){std::error_code ec;std::filesystem::remove(temp,ec);}};
 if(ArtifactCritical()){out.state=S::Critical;return nil;}
 if(!LocalAot::Directory(*root,false)||![fresh serializeToURL:[NSURL fileURLWithPath:@(temp.c_str())] error:&native_error]){out.state=S::Failed;error=FirstLine(native_error);return state;}
 auto bytes=LocalAot::Read(temp,LocalAot::MaxLibrary);if(!bytes){out.state=S::Failed;error="artifact size/integrity budget";return state;}
 const auto digest=LocalAot::Sha(bytes.bytes,bytes.length);const u64 archive_bytes=bytes.length;bytes=nil;state=nil;fresh=nil;
 // Candidate must strictly instantiate THIS descriptor before its receipt can
 // replace any successful pointer. Normal fallback is never reported as ready.
 auto*check_desc=[MTLBinaryArchiveDescriptor new];check_desc.url=[NSURL fileURLWithPath:@(temp.c_str())];
 auto check=[device newBinaryArchiveWithDescriptor:check_desc error:nil];
 descriptor.binaryArchives=check?@[check]:@[];
 id verified=check?create(MTLPipelineOptionFailOnBinaryArchiveMiss,&native_error):nil;
 descriptor.binaryArchives=@[];
 if(!verified){out.state=S::Failed;error=FirstLine(native_error);return state;}
 if(ArtifactCritical()){out.state=S::Critical;return nil;}
 const auto name=std::string(key)+"-"+digest+"-"+suffix+".binaryarchive";const auto published=*root/name;
 // Generation names are unique. Existing successful native files are retained.
 if(renamex_np(temp.c_str(),published.c_str(),RENAME_EXCL)!=0){out.state=S::Failed;error="artifact candidate publish failed";return state;}
 keep=true;
 NSDictionary*receipt=@{@"schema":@1,@"recipe_key":@(std::string(key).c_str()),@"preparation":@(std::string(preparation).c_str()),@"archive":@(name.c_str()),@"archive_bytes":@(archive_bytes),@"archive_sha256":@(digest.c_str())};
 auto json=[NSJSONSerialization dataWithJSONObject:receipt options:NSJSONWritingSortedKeys error:nil];
 if(!ArtifactReplace(*root/(std::string(key)+".json"),json)){out.state=S::Failed;error="artifact receipt publish failed; prior success retained";return state;}
 std::string committed_sha;const auto committed=ArtifactInput(*root,key,preparation,&committed_sha);
 if(!committed||*committed!=published||committed_sha!=digest){out.state=S::Failed;error="artifact committed receipt cannot be revalidated";return nil;}
 RememberPreparedArtifact(*root,key,preparation);
 out.state=S::Built;out.archive_sha256=std::move(committed_sha);return verified;
}

void* PsoCache::CreateArtifactPipeline(void*raw,bool compute,std::string_view key,bool prepare,
 HostPso::ArtifactOutcome&out,std::string&error,bool allow_compile){
 id state=nil;out={};
 SCOPE_EXIT{
  ++counters.artifact_attempts;if(out.compiled)++counters.artifact_compile_attempts;
  using S=HostPso::ArtifactState;
  switch(out.state){case S::Hit:++counters.artifact_hits;break;case S::Built:++counters.artifact_built;break;
   case S::Deferred:++counters.artifact_deferred;break;case S::Failed:++counters.artifact_failed;break;
   case S::Critical:++counters.artifact_critical;break;case S::Unavailable:++counters.artifact_unavailable;break;}
 };
 try {
 if(compute){
  auto*d=(__bridge MTLComputePipelineDescriptor*)raw;if(!d||!d.computeFunction)return nullptr;
  state=PsoArtifactCreate(impl->mtl_device,d,archive_path.parent_path(),preparation_identity,key,prepare,allow_compile,impl->archive!=nil,out,error,
    [&](MTLPipelineOption o,NSError**e){BbMetalPhase::Call bb_metal_call_132{BbMetalPhase::PsoKind(true, (std::uint64_t(o) & std::uint64_t(MTLPipelineOptionFailOnBinaryArchiveMiss)) != 0)}; id<MTLComputePipelineState> bb_metal_result=[impl->mtl_device newComputePipelineStateWithDescriptor:d options:o reflection:nil error:e]; bb_metal_call_132.Finish(bool(bb_metal_result)); return bb_metal_result;},
    [](id<MTLBinaryArchive>a,MTLComputePipelineDescriptor*d,NSError**e){return BbMetalPhase::Invoke(BbMetalPhase::Kind::ArchiveCompute, [&] { return [a addComputePipelineFunctionsWithDescriptor:d error:e]; });});
 }else{
  auto*d=(__bridge MTLRenderPipelineDescriptor*)raw;if(!d||!d.vertexFunction)return nullptr;
  state=PsoArtifactCreate(impl->mtl_device,d,archive_path.parent_path(),preparation_identity,key,prepare,allow_compile,impl->archive!=nil,out,error,
    [&](MTLPipelineOption o,NSError**e){BbMetalPhase::Call bb_metal_call_137{BbMetalPhase::PsoKind(false, (std::uint64_t(o) & std::uint64_t(MTLPipelineOptionFailOnBinaryArchiveMiss)) != 0)}; id<MTLRenderPipelineState> bb_metal_result=[impl->mtl_device newRenderPipelineStateWithDescriptor:d options:o reflection:nil error:e]; bb_metal_call_137.Finish(bool(bb_metal_result)); return bb_metal_result;},
    [](id<MTLBinaryArchive>a,MTLRenderPipelineDescriptor*d,NSError**e){return BbMetalPhase::Invoke(BbMetalPhase::Kind::ArchiveRender, [&] { return [a addRenderPipelineFunctionsWithDescriptor:d error:e]; });});
 }
 }catch(...){
  out.state=HostPso::ArtifactState::Failed;
  if(!prepare&&allow_compile&&!state){
   out.compiled=true;NSError*e=nil;
   if(compute){auto*d=(__bridge MTLComputePipelineDescriptor*)raw;d.binaryArchives=@[];BbMetalPhase::Call bb_metal_call_144{BbMetalPhase::PsoKind(true, (std::uint64_t(MTLPipelineOptionNone) & std::uint64_t(MTLPipelineOptionFailOnBinaryArchiveMiss)) != 0)}; state=[impl->mtl_device newComputePipelineStateWithDescriptor:d options:MTLPipelineOptionNone reflection:nil error:&e]; bb_metal_call_144.Finish(bool(state));}
   else{auto*d=(__bridge MTLRenderPipelineDescriptor*)raw;d.binaryArchives=@[];BbMetalPhase::Call bb_metal_call_145{BbMetalPhase::PsoKind(false, (std::uint64_t(MTLPipelineOptionNone) & std::uint64_t(MTLPipelineOptionFailOnBinaryArchiveMiss)) != 0)}; state=[impl->mtl_device newRenderPipelineStateWithDescriptor:d options:MTLPipelineOptionNone reflection:nil error:&e]; bb_metal_call_145.Finish(bool(state));}
  }
 }
 // Caller owns its ordinary archive-hit accounting; do not double count here.
 std::printf("Metal native pipeline artifact: key=%.*s state=%u prepare=%d\n",int(key.size()),key.data(),unsigned(out.state),prepare);
 return state?(__bridge_retained void*)state:nullptr;
}
std::string PsoCache::HostPreparationCursor()const{
 auto root=ArtifactRoot(archive_path.parent_path(),preparation_identity,false);if(!root)return {};
 auto json=LocalAot::Json(*root/"progress.json");if(!json||!LocalAot::Integer(json,@"schema",1)||!LocalAot::String(json,@"preparation",preparation_identity))return {};
 id value=json[@"after"];if(![value isKindOfClass:[NSString class]])return {};const std::string result=[value UTF8String];return LocalAot::Hex(result)?result:std::string{};
}
void PsoCache::HostPreparationProgress(const HostPso::WarmResult&r,std::string_view after){
 auto root=ArtifactRoot(archive_path.parent_path(),preparation_identity,true);if(!root)return;
 NSDictionary*value=@{@"schema":@1,@"preparation":@(preparation_identity.c_str()),@"state":@(unsigned(r.state)),@"known":@(r.known),@"processed":@(r.processed),@"ready":@(r.ready),@"built":@(r.built),@"failed":@(r.failed),@"rejected":@(r.rejected),@"deferred":@(r.deferred),@"catalog_complete":@(r.catalog_complete),@"after":@(std::string(after).c_str())};
 ArtifactReplace(*root/"progress.json",[NSJSONSerialization dataWithJSONObject:value options:NSJSONWritingSortedKeys error:nil]);
}
