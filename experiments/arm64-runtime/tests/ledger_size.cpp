#include <cstdio>
#include "video_core/buffer_cache/preserved_writeback.h"
int main(){using L=VideoCore::PreservedWritebackLedger;std::printf("ledger=%zu snapshot=%zu ticket=%zu capacity=%zu\n",sizeof(L),sizeof(L::Snapshot),sizeof(L::Ticket),L::Capacity);}
