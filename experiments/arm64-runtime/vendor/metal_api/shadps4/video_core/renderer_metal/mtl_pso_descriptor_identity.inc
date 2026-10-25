// SPDX-License-Identifier: GPL-2.0-or-later
namespace {
std::string HostJsonHash(NSDictionary* object) {
 if(!object)return {};auto data=[NSJSONSerialization dataWithJSONObject:object options:NSJSONWritingSortedKeys error:nil];
 return data?LocalAot::Sha(data.bytes,data.length):std::string{};
}
bool HostNoLinked(MTLLinkedFunctions* linked) {
 return !linked.functions.count&&!linked.privateFunctions.count&&!linked.binaryFunctions.count&&!linked.groups.count;
}
std::string DescriptorFingerprint(id object) {
 NSMutableDictionary* out=[NSMutableDictionary dictionary];
 if([object isKindOfClass:[MTLComputePipelineDescriptor class]]) {
  MTLComputePipelineDescriptor*d=object;
  if(d.preloadedLibraries.count||!HostNoLinked(d.linkedFunctions))return {};
  if(d.stageInputDescriptor){auto*i=d.stageInputDescriptor;NSMutableArray*a=[NSMutableArray array],*b=[NSMutableArray array];for(unsigned n=0;n<31;++n){auto*x=i.attributes[n];auto*y=i.layouts[n];[a addObject:@[@(x.format),@(x.offset),@(x.bufferIndex)]];[b addObject:@[@(y.stride),@(y.stepFunction),@(y.stepRate)]];}out[@"stage_input"]=@[@(i.indexType),@(i.indexBufferIndex),a,b];}
  out[@"kind"]=@"compute";
  out[@"multiple"]=@(d.threadGroupSizeIsMultipleOfThreadExecutionWidth);out[@"max_threads"]=@(d.maxTotalThreadsPerThreadgroup);
  out[@"indirect"]=@(d.supportIndirectCommandBuffers);out[@"adding"]=@(d.supportAddingBinaryFunctions);out[@"stack"]=@(d.maxCallStackDepth);
  NSMutableArray* buffers=[NSMutableArray array];for(unsigned i=0;i<31;++i)[buffers addObject:@(d.buffers[i].mutability)];out[@"buffers"]=buffers;
  if(@available(macOS 15.0,*))out[@"validation"]=@(d.shaderValidation);
  if(@available(macOS 26.0,*)){const auto n=d.requiredThreadsPerThreadgroup;out[@"required"]=@[@(n.width),@(n.height),@(n.depth)];}
  if(@available(macOS 27.0,*)){out[@"forward"]=@(d.forwardProgressUsage);out[@"contention"]=@(d.contentionRelief);out[@"persistent"]=@(d.optimizeForPersistentKernel);}
 } else if([object isKindOfClass:[MTLRenderPipelineDescriptor class]]) {
  MTLRenderPipelineDescriptor*d=object;
  if(d.vertexPreloadedLibraries.count||d.fragmentPreloadedLibraries.count||!HostNoLinked(d.vertexLinkedFunctions)||!HostNoLinked(d.fragmentLinkedFunctions))return {};
  if(d.vertexDescriptor){auto*v=d.vertexDescriptor;NSMutableArray*a=[NSMutableArray array],*b=[NSMutableArray array];for(unsigned n=0;n<31;++n){auto*x=v.attributes[n];auto*y=v.layouts[n];[a addObject:@[@(x.format),@(x.offset),@(x.bufferIndex)]];[b addObject:@[@(y.stride),@(y.stepFunction),@(y.stepRate)]];}out[@"vertex_input"]=@[a,b];}
  out[@"kind"]=@"render";
  out[@"samples"]=@(d.rasterSampleCount);out[@"alpha_coverage"]=@(d.alphaToCoverageEnabled);out[@"alpha_one"]=@(d.alphaToOneEnabled);out[@"rasterize"]=@(d.rasterizationEnabled);
  out[@"amplification"]=@(d.maxVertexAmplificationCount);out[@"depth"]=@(d.depthAttachmentPixelFormat);out[@"stencil"]=@(d.stencilAttachmentPixelFormat);out[@"topology"]=@(d.inputPrimitiveTopology);
  out[@"tess"]=@[@(d.tessellationPartitionMode),@(d.maxTessellationFactor),@(d.tessellationFactorScaleEnabled),@(d.tessellationFactorFormat),@(d.tessellationControlPointIndexType),@(d.tessellationFactorStepFunction),@(d.tessellationOutputWindingOrder)];
  out[@"indirect"]=@(d.supportIndirectCommandBuffers);out[@"adding"]=@[@(d.supportAddingVertexBinaryFunctions),@(d.supportAddingFragmentBinaryFunctions)];out[@"stack"]=@[@(d.maxVertexCallStackDepth),@(d.maxFragmentCallStackDepth)];
  NSMutableArray* colors=[NSMutableArray array];for(unsigned i=0;i<8;++i){auto*c=d.colorAttachments[i];[colors addObject:@[@(c.pixelFormat),@(c.blendingEnabled),@(c.sourceRGBBlendFactor),@(c.destinationRGBBlendFactor),@(c.rgbBlendOperation),@(c.sourceAlphaBlendFactor),@(c.destinationAlphaBlendFactor),@(c.alphaBlendOperation),@(c.writeMask)]];}out[@"colors"]=colors;
  NSMutableArray* buffers=[NSMutableArray array];for(unsigned i=0;i<31;++i)[buffers addObject:@[@(d.vertexBuffers[i].mutability),@(d.fragmentBuffers[i].mutability)]];out[@"buffers"]=buffers;
  if(@available(macOS 15.0,*))out[@"validation"]=@(d.shaderValidation);
 }else return {};
 // Functions are represented by source/options/entry/constants, not native addresses.
 // binaryArchives is lookup state, not compilation semantics. Labels only matter
 // for the explicit validation filter below. Without filters debug labels are not variants.
 const auto* enabled=std::getenv("MTL_SHADER_VALIDATION_ENABLE_PIPELINES");
 const auto* disabled=std::getenv("MTL_SHADER_VALIDATION_DISABLE_PIPELINES");
 if((enabled&&*enabled)||(disabled&&*disabled))out[@"validation_label"]=[object label]?:@"";
 for(const char*key:{"MTL_SHADER_VALIDATION","MTL_SHADER_VALIDATION_ENABLE_PIPELINES","MTL_SHADER_VALIDATION_DISABLE_PIPELINES"}) {
  const auto*value=std::getenv(key);out[@(key)]=value?@(value):@"<unset>";
 }
 return HostJsonHash(out);
}
}
std::string PsoCache::ArtifactDescriptorIdentity(void*raw,bool compute){id object=(__bridge id)raw;if(!object||(compute&&![object isKindOfClass:[MTLComputePipelineDescriptor class]])||(!compute&&![object isKindOfClass:[MTLRenderPipelineDescriptor class]]))return {};return DescriptorFingerprint(object);}
