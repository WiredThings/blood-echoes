// SPDX-License-Identifier: GPL-2.0-or-later

#include "video_core/renderer_metal/mtl_vertex_metadata.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <sys/mman.h>
#include <unistd.h>
#include "shader_recompiler/info.h"
using namespace VideoCore;
struct TestPipeline : PipelineBase {
    TestPipeline(const Shader::Info& info,const Shader::Gcn::FetchShaderData& fetch)
      : PipelineBase({},std::optional<const Shader::Gcn::FetchShaderData>{fetch}) {
        stages[u32(Shader::SwStage::Vertex)]=&info;
    }
};
std::vector<Metal::VertexAttribute> OriginalMetadata(const TestPipeline& pipeline,u32 step0,u32 step1) {
    VertexInputs<vk::VertexInputAttributeDescription> attributes;
    VertexInputs<vk::VertexInputBindingDescription> bindings;
    VertexInputs<vk::VertexInputBindingDivisorDescriptionEXT> divisors;
    VertexInputs<AmdGpu::Buffer> buffers;
    pipeline.GetVertexInputs(attributes,bindings,divisors,buffers,step0,step1);
    std::vector<Metal::VertexAttribute> result;
    for(size_t i=0;i<attributes.size();++i){
        Metal::VertexAttribute attribute;
        attribute.location=attributes[i].location;
        attribute.per_instance=bindings[i].inputRate==vk::VertexInputRate::eInstance;
        if(attribute.per_instance)for(const auto& divisor:divisors)
            if(divisor.binding==bindings[i].binding){attribute.divisor=divisor.divisor;}
        result.push_back(attribute);
    }
    return result;
}
int main(int argc,char**) {
    Shader::Info info{};
    std::array<u32,16> words{};
    info.user_data=words;
    AmdGpu::Buffer sharp{};sharp.base_address=0x1000;sharp.num_records=8;
    sharp.data_format=11;sharp.num_format=7;
    std::memcpy(words.data(),&sharp,sizeof(sharp));
    Shader::Gcn::FetchShaderData fetch;
    const auto attribute=[](u8 semantic,u8 instance){return Shader::Gcn::VertexAttribute{
        .semantic=semantic,.dest_vgpr=0,.num_elements=2,.sgpr_base=Shader::IR::NumScalarRegs,
        .dword_offset=0,.instance_data=instance,.inst_offset=0};};
    if(argc>1){
        void* none=mmap(nullptr,4096,PROT_NONE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(none!=MAP_FAILED);
        std::memcpy(words.data(),&none,sizeof(none));fetch.attributes.push_back(attribute(0,1));fetch.attributes[0].sgpr_base=0;
        TestPipeline pipeline(info,fetch);OriginalMetadata(pipeline,2,3);
        assert(false); // the original metadata-only call unnecessarily dereferences PROT_NONE
    }
    unsigned cases=0;
    const auto compare=[&](u32 step0,u32 step1){
        TestPipeline pipeline(info,fetch);
        assert(OriginalMetadata(pipeline,step0,step1)==Metal::VertexMetadataFromFetch(&fetch,step0,step1));++cases;
    };
    assert(Metal::VertexMetadataFromFetch(nullptr,0,0).empty());
    compare(0,0);
    for(unsigned rate=0;rate<256;++rate)for(auto step:std::array<std::array<u32,2>,5>{{{0,0},{1,1},{2,3},{31,63},{~0u,7}}}){
        fetch.attributes={attribute(3,u8(rate))};compare(step[0],step[1]);
    }
    u32 random=0x146;
    const auto next=[&]{random^=random<<13;random^=random>>17;random^=random<<5;return random;};
    for(unsigned c=0;c<4096;++c){fetch.attributes.clear();for(unsigned n=next()%33;n;--n)fetch.attributes.push_back(attribute(u8(next()%8),u8(next())));compare(next(),next());}
    fetch.attributes={attribute(2,1),attribute(2,2),attribute(2,0)};
    compare(2,7);
    const auto duplicate=Metal::VertexMetadataFromFetch(&fetch,2,7);
    assert(duplicate[0].divisor==7 && duplicate[1].divisor==7 && !duplicate[2].per_instance);
    // The new interface has no Info, table pointer, descriptor or stride dependency.
    void* none=mmap(nullptr,4096,PROT_NONE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(none!=MAP_FAILED);
    std::memcpy(words.data(),&none,sizeof(none));fetch.attributes={attribute(0,1),attribute(1,2)};
    for(auto& a:fetch.attributes)a.sgpr_base=0;
    const auto metadata=Metal::VertexMetadataFromFetch(&fetch,2,3);
    assert(metadata.size()==2 && metadata[0].divisor==2 && metadata[1].divisor==3);
    assert(munmap(none,4096)==0);
    std::printf("METAL_VERTEX_METADATA exact-original-cases=%u; null/empty,all256 step values,duplicates,zero/max divisors,32 attributes,PROT_NONE metadata-only passed\n",cases);
}
