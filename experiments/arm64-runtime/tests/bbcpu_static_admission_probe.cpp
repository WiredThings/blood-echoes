// SPDX-License-Identifier: GPL-2.0-or-later
#include "bbcpu_static_admission.h"
#include <iostream>
int main(int argc,char** argv){
 try{
  if(argc==2&&std::string_view(argv[1])=="--abi"){std::cout<<BBCPU_ABI_IDENTITY<<'\n';return 0;}
  if(argc!=3&&argc!=4)throw std::runtime_error("authored boot and artifact directory required");
  BbBoot::Boot boot(argv[1]);std::vector<unsigned char> image(size_t(boot.size));boot.load(image.data());BbCpuImage::RelocateMain(image.data(),boot);
  if(argc==4){if(std::string_view(argv[3])!="--mutate-code")throw std::runtime_error("unknown control");image.at(4100)^=0x28;}
  const auto root=std::filesystem::canonical(argv[2]);auto result=BbCpuArtifact::Admit(boot,image.data(),root/"librecomp.dylib",[&](const auto& path){return path.parent_path()==root;});
  std::cout<<"BBCPU static receipt, ARM64, true image and pinned library admitted "<<result.effective_identity<<'\n';return 0;
 }catch(const std::exception& e){std::cerr<<"BBCPU STATIC REFUSED: "<<e.what()<<'\n';return 2;}
}
