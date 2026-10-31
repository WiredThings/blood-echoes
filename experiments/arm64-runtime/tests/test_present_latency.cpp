#include "bb_present_latency.h"
#include <cassert>
#include <string_view>
#include <thread>
int main(int argc,char**argv){
 assert(argc==2);const std::string_view mode=argv[1];
 BbPresentLatency::Origin(1000000000);
 if(mode=="budget")for(unsigned i=0;i<514;++i)BbPresentLatency::Interval("request-residence",i,1000000000,1060000000);
 else{
  BbPresentLatency::Context context{42};
  {BbPresentLatency::Phase phase{"next-drawable"};std::this_thread::sleep_for(std::chrono::milliseconds(55));}
  assert(BbPresentLatency::current_token==42);
  BbPresentLatency::Interval("present-dispatch",42,1000000000,1000000001);
 }
}
