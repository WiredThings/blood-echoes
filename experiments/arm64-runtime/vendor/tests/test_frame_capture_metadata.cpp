#include "video_core/frame_capture.h"
#include <cassert>
#include <array>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <sstream>
#include <unistd.h>
int main(){
 char temp[]="/private/tmp/bb-native-capture-test-XXXXXX";char* dir=mkdtemp(temp);assert(dir);
 const std::filesystem::path root=dir, trigger=root/"trigger";
 setenv("BB_CAPTURE_NATIVE","1",1);setenv("BB_CAPTURE_DIR",dir,1);setenv("BB_CAPTURE_TRIGGER",trigger.c_str(),1);
 using VideoCore::FrameCapture;
 unsigned drains=0;
 FrameCapture::SetNativeBoundaryDrain(&drains,[](void* p){++*static_cast<unsigned*>(p);if(FrameCapture::NativeRecording())FrameCapture::Native("{\"op\":\"close\",\"kind\":1}");});
 std::ofstream(trigger)<<"1";FrameCapture::OnFlip(123);
 assert(FrameCapture::Active()&&!FrameCapture::NativeRecording());
 FrameCapture::FrameBoundary(10);assert(FrameCapture::NativeRecording());
 FrameCapture::Native("{\"op\":\"open\",\"kind\":1}");FrameCapture::NativeLabel("quote\"\\\n",123);
 FrameCapture::Buffer(1,0,1,reinterpret_cast<void*>(1),1024); // must not dereference contents
 FrameCapture::NativeWriteOrigin(4096,192,FrameCapture::WriteOrigin::ConstantRam,768);
 FrameCapture::NativeWriteOrigin(4096,192,FrameCapture::WriteOrigin::GraphicsWriteData);
 FrameCapture::NativeWriteOrigin(4096,192,FrameCapture::WriteOrigin::ComputeWriteData);
 FrameCapture::NativeWriteOrigin(4096,192,FrameCapture::WriteOrigin::GraphicsWriteData,0,reinterpret_cast<void*>(4242));
 FrameCapture::NativeTaskPacket(true,7,8192,reinterpret_cast<void*>(1),reinterpret_cast<void*>(2),208);
 FrameCapture::NativeTaskPacket(false,7,8192,nullptr,reinterpret_cast<void*>(2),208);
 FrameCapture::NativeTaskPacket(true,7,9000,reinterpret_cast<void*>(1),reinterpret_cast<void*>(2),207);
 FrameCapture::NativeTaskPacket(true,7,16384,reinterpret_cast<void*>(3),reinterpret_cast<void*>(2),208); // same ring pointer, new position
 FrameCapture::NativeCommandSnapshot(1000,232,4096); // fake pointers must not be dereferenced
 FrameCapture::NativeCommandSnapshot(~uint64_t{0}-10,32,4096); // invalid range, explicit
 FrameCapture::NativeCommandSnapshot(1000,0,4096); // empty streams do not publish
 FrameCapture::NativeSubmittedPacket(true,1024,208,1000,232,4096,71); // exact end, no dereference
 FrameCapture::NativeSubmittedPacket(true,1025,208,1000,232,4096,72); // one byte outside
 FrameCapture::NativeSubmittedPacket(true,1024,208,1000,232,~uint64_t{0}-10,73); // translated overflow
 FrameCapture::NativeSubmittedPacket(true,1024,208,1000,~uint64_t{0},4096,74); // source overflow
 FrameCapture::NativeSubmittedPacket(false,1024,208,1024,208,1024,75);
 FrameCapture::NativeSubmittedPacket(true,1024,207,1000,232,4096,76); // wrong class
 FrameCapture::NativeWriteOrigin(1,191,FrameCapture::WriteOrigin::ConstantRam,999);
 FrameCapture::NativeWriteOrigin(1,192,static_cast<FrameCapture::WriteOrigin>(99),999);
 std::array<unsigned char,192> payload{};
 FrameCapture::NativeCommandWrite(4096,payload.data(),payload.size(),100,200,128,10);
 FrameCapture::NativeBufferUse(100,200,128,120,4096,7,0,true,"occlusion_snapshot");
 FrameCapture::NativeCommandWrite(4096,payload.data(),payload.size(),100,200,128,10);
 payload[1]=1;
 FrameCapture::NativeCommandWrite(4096,payload.data(),payload.size(),100,200,128,11);
 FrameCapture::NativeCommandWrite(4096,payload.data(),payload.size(),100,200,128,0);
 FrameCapture::NativeCommandWrite(4096,payload.data(),payload.size(),100,200,128,12);
 // Alias via native buffer, no guest address. Keep VS and FS distinct.
 FrameCapture::NativeBufferUse(101,200,128,192,0,3,123,false,"shader");
 FrameCapture::NativeBufferUse(101,200,128,192,0,0,124,false,"shader");
 FrameCapture::NativeBufferUse(101,200,320,192,8192,3,125,false,"nonoverlap");
 FrameCapture::NativeBufferUse(0,0,0,0,0,5,126,true,"unknown");
 FrameCapture::NativeCpuAccess(4096,192,false);
 FrameCapture::NativeCpuAccess(4287,1,true);
 FrameCapture::NativeCpuAccess(4288,192,false); // adjacent is not overlap
 // Wrong sizes and inactive capture must not even read the supplied CPU pointer.
 FrameCapture::NativeCommandWrite(0,reinterpret_cast<void*>(1),191,0,0,0,0);
 FrameCapture::FrameBoundary(11);assert(!FrameCapture::Active());
 FrameCapture::NativeCommandWrite(0,reinterpret_cast<void*>(1),192,0,0,0,0);
 FrameCapture::NativeCommandSnapshot(42,208,84); // inactive must not publish or advance identity
 FrameCapture::NativeWriteOrigin(1,192,FrameCapture::WriteOrigin::ConstantRam,998);
 auto read=[&](unsigned frame){for(auto& e:std::filesystem::directory_iterator(root))if(e.path().extension()==".txt"){std::ifstream f(e.path());auto text=std::string(std::istreambuf_iterator<char>(f),{});if(text.find("\"frame\":"+std::to_string(frame)+",")!=std::string::npos)return text;}return std::string{};};
 assert(drains==2);auto text=read(10);assert(text.find("\"op\":\"close\"")!=std::string::npos);assert(text.find("\"frame\":10")!=std::string::npos);assert(text.find("\"truncated\":false")!=std::string::npos);assert(text.find("\\u000a")!=std::string::npos);assert(text.find("\"queue\":123")!=std::string::npos);
 const auto drain_begin=text.find("\"op\":\"capture_drain_begin\"");
 const auto close=text.find("\"op\":\"close\"");
 const auto drain_end=text.find("\"op\":\"capture_drain_end\"");
 assert(drain_begin!=std::string::npos && drain_begin<close && close<drain_end);
 assert(text.find("\"boundary_frame\":11",drain_begin)!=std::string::npos);
 assert(text.find("\"op\":\"capture_drain_begin\"",drain_end)==std::string::npos);
 assert(text.find("\"same_payload\":true,\"same_generation\":true")!=std::string::npos);
 assert(text.find("\"version\":1,\"logical_stage\":7,\"shader\":0,\"write\":true,\"unknown\":false,\"source\":\"occlusion_snapshot\"")!=std::string::npos);
 assert(text.find("\"packet\":\"dump_const_ram\",\"constant_offset\":768")!=std::string::npos);
 assert(text.find("\"packet\":\"graphics_write_data\",\"constant_offset\":null")!=std::string::npos);
 assert(text.find("\"packet\":\"compute_write_data\",\"constant_offset\":null")!=std::string::npos);
 assert(text.find("\"order\":\"graphics_write_execution\"")!=std::string::npos);
 assert(text.find("\"order\":\"compute_decode\"")!=std::string::npos);
 assert(text.find("\"order\":\"constant_engine_decode\"")!=std::string::npos);
 assert(text.find("\"packet_address\":4242")!=std::string::npos);
 assert(text.find("\"op\":\"ordered_task_copy\",\"queue\":7,\"position\":8192,\"source\":1,\"payload\":2,\"bytes\":208")!=std::string::npos);
 assert(text.find("\"op\":\"ordered_task_execute\",\"queue\":7,\"position\":8192,\"source\":null,\"payload\":2,\"bytes\":208")!=std::string::npos);
 assert(text.find("\"position\":16384,\"source\":3,\"payload\":2")!=std::string::npos);
 assert(text.find("\"position\":9000")==std::string::npos);
 assert(text.find("\"payload_address\":")!=std::string::npos);
 assert(text.find("\"submission\":71,\"mapping_valid\":true,\"guest_packet\":4120")!=std::string::npos);
 for(unsigned seq:{72,73,74})assert(text.find("\"submission\":"+std::to_string(seq)+",\"mapping_valid\":false,\"guest_packet\":null")!=std::string::npos);
 assert(text.find("\"copied\":false,\"submission\":75,\"mapping_valid\":true,\"guest_packet\":1024")!=std::string::npos);
 assert(text.find("\"snapshot_begin\":1000,\"range_bytes\":232")!=std::string::npos);
 assert(text.find("\"op\":\"command_snapshot\",\"sequence\":1,\"snapshot\":1000,\"bytes\":232,\"guest\":4096,\"valid\":true")!=std::string::npos);
 assert(text.find("\"bytes\":32,\"guest\":4096,\"valid\":false")!=std::string::npos);
 assert(text.find("\"op\":\"command_snapshot\",\"sequence\":3,")==std::string::npos);
 assert(text.find("\"submission\":76,")==std::string::npos);
 assert(text.find("\"constant_offset\":999")==std::string::npos);
 assert(text.find("\"constant_offset\":998")==std::string::npos);
 assert(text.find("\"same_payload\":false,\"same_generation\":false")!=std::string::npos);
 assert(text.find("\"version\":4")!=std::string::npos);
 assert(text.find("\"version\":3,\"previous\":true,\"same_payload\":false,\"same_generation\":false")!=std::string::npos);
 assert(text.find("\"version\":4,\"previous\":true,\"same_payload\":true,\"same_generation\":false")!=std::string::npos);
 assert(text.find("\"version\":5,\"previous\":true,\"same_payload\":true,\"same_generation\":false")!=std::string::npos);
 unsigned alias_reads=0;std::istringstream lines{text};std::string line;
 while(std::getline(lines,line))if(line.find("\"op\":\"watch_access\"")!=std::string::npos && line.find("\"source\":\"shader\"")!=std::string::npos)++alias_reads;
 assert(alias_reads==2);
 assert(text.find("\"logical_stage\":3,\"shader\":123")!=std::string::npos);
 assert(text.find("\"logical_stage\":0,\"shader\":124")!=std::string::npos);
 assert(text.find("\"unknown\":true,\"source\":\"unknown\"")!=std::string::npos);
 unsigned cpu_hits=0;for(size_t n=0;(n=text.find("\"op\":\"watch_cpu\"",n))!=std::string::npos;++n)++cpu_hits;
 assert(cpu_hits==2);
 assert(text.find("\"unknown\":false,\"source\":\"nonoverlap\"")==std::string::npos);
 std::ofstream(trigger)<<"1";FrameCapture::OnFlip(456);FrameCapture::FrameBoundary(20);
 FrameCapture::NativeCommandSnapshot(2000,232,5000);
 FrameCapture::NativeBufferUse(101,200,128,192,0,3,123,false,"reset");
 FrameCapture::NativeCommandWrite(4096,payload.data(),192,100,200,128,10);
 for(unsigned i=1;i<=8;++i)FrameCapture::NativeCommandWrite(4096+4096*i,payload.data(),192,100+i,200+i,128,10);
 for(unsigned i=0;i<1030;++i)FrameCapture::NativeBufferUse(101,200,512,16,0,3,123,false,"overflow");
 FrameCapture::NativeBufferUse(101,200,128,192,0,3,123,false,"overflow_after");
 FrameCapture::FrameBoundary(21);
 assert(read(20).find("\"op\":\"command_snapshot\",\"sequence\":3,")!=std::string::npos);text=read(20);
 assert(text.find("\"version\":1,\"previous\":false")!=std::string::npos);
 assert(text.find("\"source\":\"reset\"")!=std::string::npos);
 assert(text.find("\"unknown\":false,\"source\":\"reset\"")==std::string::npos);
 assert(text.find("\"address\":4096,\"size\":192,\"resource\":100,\"native\":200,\"offset\":128,\"generation\":10,\"version\":1,\"previous\":false")!=std::string::npos);
 assert(text.find("\"unknown\":false,\"source\":\"overflow_after\"")!=std::string::npos);
 assert(text.find("\"kind\":\"watch_slots\"")!=std::string::npos);
 assert(text.find("\"kind\":\"general_uses\"")!=std::string::npos);
 std::ofstream(trigger)<<"1";FrameCapture::OnFlip(456);FrameCapture::FrameBoundary(30);
 for(unsigned i=0;i<10000;++i)FrameCapture::Native(std::string(600,'x'));
 FrameCapture::FrameBoundary(31);assert(drains==6);text=read(30);assert(text.find("\"truncated\":true")!=std::string::npos);assert(text.size()<2200000);
 setenv("BB_WATCH","0x1000,192",1);
 std::ofstream(trigger)<<"1";FrameCapture::OnFlip(789);FrameCapture::FrameBoundary(40);
 FrameCapture::NativeBufferUse(0,0,0,192,4096,5,555,false,"seed_before_write");
 FrameCapture::NativeCpuAccess(4096,192,false);
 payload.fill(0);FrameCapture::NativeCommandWrite(4096,payload.data(),192,100,200,128,10);
 FrameCapture::NativeBufferUse(100,200,128,12,4096,7,0,false,"indirect_dispatch_args");
 FrameCapture::NativeBufferUse(0,0,0,4,4096,7,0,false,"cp_graphics_wait");
 FrameCapture::NativeCpuAccess(4096,4,false);
 FrameCapture::FrameBoundary(41);assert(drains==8);text=read(40);unsetenv("BB_WATCH");
 assert(text.find("\"version\":0,\"logical_stage\":5,\"shader\":555")!=std::string::npos);
 assert(text.find("\"version\":1,\"logical_stage\":7,\"shader\":0,\"write\":false,\"unknown\":false,\"source\":\"indirect_dispatch_args\"")!=std::string::npos);
 assert(text.find("\"version\":1,\"logical_stage\":7,\"shader\":0,\"write\":false,\"unknown\":false,\"source\":\"cp_graphics_wait\"")!=std::string::npos);
 assert(text.find("\"cp_graphics_wait\":1")!=std::string::npos);
 assert(text.find("\"indirect_dispatch_args\":1")!=std::string::npos);
 assert(text.find("\"version\":1,\"previous\":false,\"same_payload\":false,\"same_generation\":false,\"zero_words\":48,\"float_one_words\":0")!=std::string::npos);
 assert(std::distance(std::filesystem::directory_iterator(root),std::filesystem::directory_iterator{})==4);
 // One trigger must capture three consecutive encoding frames, not three later
 // frames separated by host polling. Each member resets its own dependency versions.
 setenv("BB_CAPTURE_CONSECUTIVE_COUNT","3",1);
 std::ofstream(trigger)<<"1";FrameCapture::OnFlip(789);FrameCapture::FrameBoundary(50);
 for(unsigned frame=50;frame<53;++frame) {
     assert(FrameCapture::NativeRecording());
     FrameCapture::NativeCommandWrite(4096,payload.data(),192,100,200,128,10);
     FrameCapture::FrameBoundary(frame+1);
     text=read(frame);
     assert(text.find("\"series_count\":3,\"series_member\":"+std::to_string(frame-49))!=std::string::npos);
     assert(text.find("\"version\":1,\"previous\":false")!=std::string::npos);
 }
 assert(!FrameCapture::Active());
 unsetenv("BB_CAPTURE_CONSECUTIVE_COUNT");
 assert(std::distance(std::filesystem::directory_iterator(root),std::filesystem::directory_iterator{})==7);
 FrameCapture::ClearNativeBoundaryDrain(&drains);
 std::filesystem::remove_all(root);puts("metadata boundaries, buffer non-read, escaping, bounded dependency versions, aliases, stages, reset and capacity passed");
}
