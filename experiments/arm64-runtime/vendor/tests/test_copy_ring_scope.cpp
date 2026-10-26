// SPDX-License-Identifier: GPL-2.0-or-later
// Synthetic descriptors and control bytes only. No game/save/cache access.
#include "video_core/buffer_cache/copy_ring_scope.h"
#include <cassert>
#include <cstdio>
#include <random>
#include <vector>
using namespace VideoCore;
static AmdGpu::Buffer Buffer(u64 base, bool ctl) {
    AmdGpu::Buffer b{};
    b.base_address = base; b.stride = ctl ? 12 : 4;
    b.num_records = ctl ? 4096 : (1u << 27);
    b.data_format = u32(ctl ? AmdGpu::DataFormat::Format32_32_32 : AmdGpu::DataFormat::Format32);
    b.num_format = u32(AmdGpu::NumberFormat::Float);
    b.dst_sel_x = u32(AmdGpu::CompSwizzle::Red);
    b.dst_sel_y = u32(AmdGpu::CompSwizzle::Green);
    b.dst_sel_z = u32(AmdGpu::CompSwizzle::Blue);
    return b;
}
int main() {
    const std::array<AmdGpu::Buffer,3> buffers={Buffer(0x100000,true),Buffer(0x100000000,false),Buffer(0x200000000,false)};
    const CopyRingGrid grid{2,1,1,0,0,0,64,1,1,false};
    std::array<u32,6> ctl={100,200,3,1024,2048,63};
    const auto bytes=[&]{return std::span<const u8>(reinterpret_cast<const u8*>(ctl.data()),sizeof(ctl));};
    auto plan=BuildCopyRingScope(bytes(),buffers,grid);assert(plan);
    assert(plan->source.address==buffers[1].base_address+800 && plan->source.size==(2048+64)*4-800);
    assert(plan->destination.address==buffers[2].base_address+400 && plan->destination.size==(1024+64)*4-400);
    // Planner consumes the immutable snapshot, not later mutations of the guest controls.
    auto guest=ctl;guest[0]=70000;
    assert(BuildCopyRingScope(bytes(),buffers,grid)->destination.address==plan->destination.address);
    assert(!BuildCopyRingScope(bytes().first(23),buffers,grid));
    for(unsigned field=0;field<10;++field){auto g=grid;
        switch(field){case 0:g.x=0;break;case 1:g.x=4097;break;case 2:g.y=2;break;
        case 3:g.z=2;break;case 4:g.start_x=1;break;case 5:g.start_y=1;break;
        case 6:g.start_z=1;break;case 7:g.threads_x=0;break;case 8:g.threads_y=2;break;case 9:g.partial=true;break;}
        assert(!BuildCopyRingScope(bytes(),buffers,g));
    }
    for(unsigned slot=0;slot<3;++slot)for(unsigned field=0;field<9;++field){auto b=buffers;
        switch(field){case 0:b[slot].base_address=0;break;case 1:b[slot].base_address=3;break;
        case 2:b[slot].swizzle_enable=1;break;case 3:b[slot].add_tid_enable=1;break;
        case 4:b[slot].stride=16;break;case 5:b[slot].num_records=0;break;
        case 6:b[slot].data_format=u32(AmdGpu::DataFormat::Format16);break;
        case 7:b[slot].dst_sel_x=u32(AmdGpu::CompSwizzle::One);break;
        case 8:b[slot].num_format=u32(AmdGpu::NumberFormat::Unorm);break;}
        assert(!BuildCopyRingScope(bytes(),b,grid));
    }
    // The format lowering uses raw F32 loads for all three 32-bit number formats;
    // integer control words are bit patterns, without numeric conversion.
    for (auto a : {AmdGpu::NumberFormat::Float,AmdGpu::NumberFormat::Uint,AmdGpu::NumberFormat::Sint})
    for (auto b : {AmdGpu::NumberFormat::Float,AmdGpu::NumberFormat::Uint,AmdGpu::NumberFormat::Sint})
    for (auto c : {AmdGpu::NumberFormat::Float,AmdGpu::NumberFormat::Uint,AmdGpu::NumberFormat::Sint}) {
        auto bs=buffers;bs[0].num_format=u32(a);bs[1].num_format=u32(b);bs[2].num_format=u32(c);
        auto p=BuildCopyRingScope(bytes(),bs,grid);assert(p);
        assert(p->source.address==plan->source.address && p->source.size==plan->source.size);
        assert(p->destination.address==plan->destination.address && p->destination.size==plan->destination.size);
    }
    auto bad=buffers;bad[0].num_records=1;assert(!BuildCopyRingScope(bytes(),bad,grid));
    bad=buffers;bad[1].num_records=1;assert(!BuildCopyRingScope(bytes(),bad,grid));
    bad=buffers;bad[2].num_records=1;assert(!BuildCopyRingScope(bytes(),bad,grid));
    auto wrap=ctl;ctl[5]=0xffffffff;assert(!BuildCopyRingScope(bytes(),buffers,grid));ctl=wrap;
    ctl[0]=0xffffffff;assert(!BuildCopyRingScope(bytes(),buffers,grid));ctl=wrap;
    bad=buffers;bad[1].num_records=0xffffffff;assert(!BuildCopyRingScope(bytes(),bad,grid));
    bad=buffers;bad[1].base_address=(u64{1}<<40)-4;assert(!BuildCopyRingScope(bytes(),bad,grid));
    // An independent lane enumeration checks conservative coverage, including sparse,
    // overlapping, reversed, long-end and end-zero records. No shader operations are replaced.
    const CopyRingRange declared{100,100};
    for (auto scope : {CopyRingRange{100,100}, CopyRingRange{120,4}, CopyRingRange{196,4}})
        assert(CopyRingBookkeepingValid(declared,{&scope,1}));
    for (auto scope : {CopyRingRange{99,1}, CopyRingRange{100,101}, CopyRingRange{200,1},
                       CopyRingRange{120,0}, CopyRingRange{~u64{0},8}})
        assert(!CopyRingBookkeepingValid(declared,{&scope,1}));
    assert(!CopyRingBookkeepingValid(declared,{}));
    std::array<CopyRingRange,2> unsorted={CopyRingRange{150,4},CopyRingRange{120,4}};
    assert(!CopyRingBookkeepingValid(declared,unsorted));
    std::vector<CopyRingRange> union_ranges={{0x30004,4},{0x10004,8},{0x10008,8},{0x10010,4},{0x30000,4}};
    MergeCopyRingRanges(union_ranges);assert(union_ranges.size()==2);
    assert(union_ranges[0].address==0x10004 && union_ranges[0].size==16);
    std::vector<CopyRingRange> pages;
    ForEachCopyRingPageRange(union_ranges,16,[&](u64 a,u64 n){pages.push_back({a,n});});
    assert(pages.size()==2 && pages[0].address==0x10000 && pages[0].size==65536 && pages[1].address==0x30000);
    pages.clear();ForEachCopyRingPageRange(union_ranges,21,[&](u64 a,u64 n){pages.push_back({a,n});});
    assert(pages.size()==1 && pages[0].address==0 && pages[0].size==(1u<<21));
    const auto contains=[](const auto& ranges,u64 address) {
        const auto end=std::upper_bound(ranges.begin(),ranges.end(),address,
            [](u64 at,const auto& range){return at<range.address;});
        if(end==ranges.begin())return false;
        const auto& r=*std::prev(end);return address>=r.address && address+4<=r.address+r.size;
    };
    std::mt19937 random(0x632021);u64 loads=0,stores=0;
    for(unsigned run=0;run<10000;++run){
        auto g=grid;g.x=1+random()%32;g.threads_x=1+random()%1024;
        std::vector<u32> controls(g.x*3);
        for(u32 i=0;i<g.x;++i){controls[i*3]=random()%100000;controls[i*3+1]=random()%100000;controls[i*3+2]=random()%2048;}
        auto p=BuildCopyRingScope({reinterpret_cast<const u8*>(controls.data()),controls.size()*4},buffers,g);assert(p);
        assert(CopyRingBookkeepingValid({buffers[1].base_address,buffers[1].GetSize()},p->sources));
        assert(CopyRingBookkeepingValid({buffers[2].base_address,buffers[2].GetSize()},p->destinations));
        for(u32 group=0;group<g.x;++group)for(u32 lane=0;lane<g.threads_x;++lane){
            if(lane>controls[group*3+2])continue;
            const u64 src=buffers[1].base_address+u64(controls[group*3+1]+lane)*4;
            const u64 dst=buffers[2].base_address+u64(controls[group*3]+lane)*4;
            assert(contains(p->sources,src));++loads;
            assert(contains(p->destinations,dst));++stores;
        }
    }
    std::printf("copy ring scope: 10000 layouts, %llu reads and %llu writes covered; invalid layouts fall back\n",(unsigned long long)loads,(unsigned long long)stores);
}
