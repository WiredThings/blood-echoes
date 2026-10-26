#include "video_core/renderer_metal/mtl_phase_counters.h"
#include <cassert>
#include <cstdio>
#include <stdexcept>
#include <thread>
#include <vector>
using namespace BbMetalPhase;
static unsigned checks=0;void Check(bool v){++checks;assert(v);}
int main(int argc,char**argv){
 setenv("BB_FRAME_LOG_ONLY","1",1);
 Check(sizeof(values)<1024);Check(sizeof(Snapshot)==Count*4*sizeof(std::uint64_t));
 Check(PsoKind(false,false)==Kind::Render);Check(PsoKind(true,false)==Kind::Compute);Check(PsoKind(false,true)==Kind::StrictRender);Check(PsoKind(true,true)==Kind::StrictCompute);
 Invoke(Kind::SourceLibrary,[]{return true;});Invoke(Kind::SourceLibrary,[]{return false;});
 Invoke(Kind::LocalLibraryData,[]{return true;});Invoke(Kind::LocalLibraryData,[]{return false;});
 Invoke(Kind::StrictRender,[]{return false;});Invoke(Kind::StrictCompute,[]{return true;});
 Invoke(Kind::ArchiveRender,[]{return true;});Invoke(Kind::ArchiveCompute,[]{return false;});
 Call outstanding{Kind::Compute};MarkGpuReady();Check(phase.load()==Phase::Runtime);Check(!startup_reported.load());
 Invoke(Kind::SourceLibrary,[]{return true;});
 outstanding.Finish(true);outstanding.Finish(false);Check(startup_reported.load());const auto startup=Read(Phase::PreGpuReady);
 Check(startup[unsigned(Kind::SourceLibrary)].calls==2);Check(startup[unsigned(Kind::SourceLibrary)].ok==1);Check(startup[unsigned(Kind::SourceLibrary)].failed==1);
 Check(startup[unsigned(Kind::Compute)].calls==1&&startup[unsigned(Kind::Compute)].ok==1&&startup[unsigned(Kind::Compute)].failed==0);
 try{Invoke(Kind::Render,[]()->bool{throw std::runtime_error("author");});Check(false);}catch(const std::runtime_error&){Check(true);}
 Invoke(Kind::StrictRender,[]{return true;});Invoke(Kind::StrictCompute,[]{return false;});
 Invoke(Kind::LocalLibraryData,[]{return false;});Invoke(Kind::ArchiveCompute,[]{return true;});
 std::thread worker([]{for(unsigned i=0;i<200;++i)Invoke(Kind::Compute,[i]{return i%2==0;});});
 for(unsigned i=0;i<200;++i)Invoke(Kind::Render,[]{return true;});worker.join();MarkGpuReady();
 const auto runtime=Read(Phase::Runtime);Check(runtime[unsigned(Kind::SourceLibrary)].calls==1);Check(runtime[unsigned(Kind::SourceLibrary)].ok==1);
 Check(runtime[unsigned(Kind::Render)].calls==201);Check(runtime[unsigned(Kind::Render)].ok==200);Check(runtime[unsigned(Kind::Render)].failed==1);
 Check(runtime[unsigned(Kind::Compute)].calls==200);Check(runtime[unsigned(Kind::Compute)].ok==100);Check(runtime[unsigned(Kind::Compute)].failed==100);
 Check(runtime[unsigned(Kind::StrictRender)].calls==1&&runtime[unsigned(Kind::StrictRender)].ok==1);
 Check(runtime[unsigned(Kind::StrictCompute)].calls==1&&runtime[unsigned(Kind::StrictCompute)].failed==1);
 const auto startup_after=Read(Phase::PreGpuReady);for(unsigned i=0;i<Count;++i)Check(startup[i].calls==startup_after[i].calls&&startup[i].ok==startup_after[i].ok&&startup[i].failed==startup_after[i].failed&&startup[i].api_ns==startup_after[i].api_ns);
 Check(argc==2);FILE*f=std::fopen(argv[1],"w");Check(f);std::fputs("author_frame",f);CsvHeader(f);std::fputc('\n',f);Snapshot previous{};std::fputc('1',f);CsvDelta(f,previous);std::fputc('\n',f);std::fputc('2',f);CsvDelta(f,previous);std::fputc('\n',f);std::fclose(f);
 std::printf("METAL_PHASE_COUNTER_CPU checks=%u no GPU, source/data/strict/ordinary/archive/phase/concurrency/exception/CSV\n",checks);
}
