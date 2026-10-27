// SPDX-License-Identifier: GPL-3.0-or-later
// Included inside ArmTravel ONLY by the private test source overlay.
BbPrivateDeath::Gate death_gate;
U death_entry{};
void PrepareDeath(const Mapping& image,const Boot& boot,Mapping& stub,size_t used){
 check(used<8192&&boot.covered(BbPrivateDeath::SetHpOffset,BbPrivateDeath::Signature.size(),true),"private SetHp outside executable boot");
 const auto thunk=BbPrivateDeath::Thunk(image.at()+BbPrivateDeath::SetHpOffset,static_cast<const unsigned char*>(image.ptr)+BbPrivateDeath::SetHpOffset,BbPrivateDeath::Signature.size());
 check(8192+thunk.size()<=stub.size,"private SetHp stub capacity");memcpy(static_cast<unsigned char*>(stub.ptr)+8192,thunk.data(),thunk.size());death_entry=stub.at()+8192;
 printf("ARM PRIVATE DEATH checked SetHp guest thunk installed; at most two commands, no direct HP writes\n");
}
void ObserveDeath(double now,int world,int loading,U player){
 int hp=-1,max=-1,mhp=-1,mmax=-1;
 const U chr=player?Ptr(player+0x3b0):0,stats=chr?Ptr(chr+0x20):0;
 const U data=Ptr(game+0x553b130),mirror=data?Ptr(data+8):0;
 bool valid=stats&&mirror&&Read(stats+0xf8,&hp,4)&&Read(stats+0xfc,&max,4)&&Read(mirror+0x14,&mhp,4)&&Read(mirror+0x18,&mmax,4)&&BbPrivateDeath::Health(hp,max,mhp,mmax);
 if(read_failures)valid=false;
 death_gate.Observe(player,world,loading,valid,hp);
 char data_text[64]{};ssize_t n{};unsigned command{};char extra{};
 if(File(directory/"death-command.txt",data_text,sizeof(data_text),n)){
  if(sscanf(data_text,"%u %c",&command,&extra)!=1){state.stage=WW_FAILED;state.error="invalid-death-command";}
  else if(command>death_gate.sequence){
   if((state.stage!=WW_IDLE&&state.stage!=WW_ARRIVED)||!death_gate.Request(command,world,loading,valid,hp)){state.stage=WW_FAILED;state.error="rejected-death-command";}
   else {printf("ARM PRIVATE DEATH sequence=%u hp=%d max=%d\n",command,hp,max);const U args[]={stats+0xf0,0,1};bb_arm_call(death_entry,args,3);if(!Read(stats+0xf8,&hp,4))valid=false;}
  }
 }
 const auto temp=directory/"death-status.pending",dest=directory/"death-status.json";
 int fd=open(temp.c_str(),O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW|O_CLOEXEC,0600);if(fd<0){state.stage=WW_FAILED;state.error="death-status-open";return;}
 char output[640];int count=snprintf(output,sizeof(output),"{\"schema\":1,\"time\":%.6f,\"health_valid\":%s,\"hp\":%d,\"max_hp\":%d,\"player\":%llu,\"stable\":%u,\"death_sequence\":%u,\"world\":%s,\"loading\":%s,\"error\":\"%s\"}\n",now,valid?"true":"false",hp,max,player,death_gate.stable,death_gate.sequence,world?"true":"false",loading?"true":"false",state.error?state.error:"");
 bool ok=count>0&&count<int(sizeof(output))&&write(fd,output,count)==count;if(close(fd))ok=false;if(!ok||rename(temp.c_str(),dest.c_str())){state.stage=WW_FAILED;state.error="death-status-write";}
}
