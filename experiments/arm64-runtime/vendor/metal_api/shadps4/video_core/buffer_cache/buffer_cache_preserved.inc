// SPDX-License-Identifier: GPL-2.0-or-later
// Included once from buffer_cache.cpp; uses its actual WriteTicks metadata.
#include "remote/bb_remote.h"
#include "remote/bb_live_writeback.h"
#include "bblayer_gpu_memory.h"
#include "bblayer_write_traps.h"
namespace VideoCore {
bool BufferCache::TryFlushPreservedWritebacks(VAddr address,u64 size,bool* touched_preserved) {
 if(touched_preserved)*touched_preserved=false;
 if(!preserved_writebacks.Count())return true;
 if(!LayerMode()||!LayerReadTrapsOn()||!BbRemote::BackActive()){
  auto scope=preserved_writebacks.TryAcquire();
  return scope&&!preserved_writebacks.Overlaps(address,size);
 }
 const RawWritebackServices services{BbRemote::Back::MemoryRegion,
     BbRemote::Back::CaptureLivePayload,BbRemote::Back::WithProtectedLivePayload,
     [](BbPayload::Origin origin,VAddr at,u64 bytes,const RawLiveBody& validate){
      return BbRemote::Back::RetireReadProtection(origin,at,bytes,validate);
     }};
 return TryFlushPreservedWritebacksWithServices(address,size,services,touched_preserved);
}
bool BufferCache::TryFlushPreservedWritebacksWithServices(VAddr address,u64 size,
 const RawWritebackServices& services,bool* touched_preserved) {
 if(touched_preserved)*touched_preserved=false;
 if(!preserved_writebacks.Count())return true;
 auto owner=preserved_writebacks.TryAcquire();
 if(!owner)return false; // an actual owner transition must be queued by the caller
 if(!preserved_writebacks.Overlaps(address,size))return true;
 if(touched_preserved)*touched_preserved=true;
 if(!LayerReadTrapsOn()||!services.region||!services.capture||!services.protected_lease||!services.retire)return false;
 // Existing GPU command owner processes confirmed unmaps before attempting a
 // slice. No live lease is held; nested ownership uses this same thread.
 ProcessPendingUnmaps();LayerForgetGpuData();
 auto& layer=BbLayer::GpuMemory::Get();
 for(std::size_t slot=0;slot<PreservedWritebackLedger::Capacity;++slot){
  const auto claim=preserved_writebacks.ClaimFirst(address,size);
  if(!claim)return !preserved_writebacks.Overlaps(address,size); // Reserved is Pending, not success
  const auto s=preserved_writebacks.Claimed(*claim);bool held=true,restart=false;
  const auto restore=[&]{if(held){preserved_writebacks.Retry(*claim);held=false;}};
  const auto reacquire=[&]{held=preserved_writebacks.ClaimExact(*claim);return held;};
  try {
   // One range per pass. No permanent fragment count cutoff: every successful
   // pass subtracts a nonempty current GPU-only slice, so progress is finite.
   for(;;){
    VAddr from=0,to=0;
    gpu_modified_ranges.ForEachInRange(s.address,s.size,[&](VAddr a,VAddr b){if(!to){from=a;to=b;}});
    if(!to)break;
    uintptr_t map_start=0,map_end=0;int mapped=0;
    if(!services.region(from,&map_start,&map_end,&mapped)||!mapped||map_end<=from){restore();return false;}
    to=std::min<VAddr>(to,map_end);const u64 bytes=to-from;
    BbPayload::Origin current{};bool observed=services.capture(from,bytes,[&](const auto&r){current={r.owner,r.mapping};return true;});
    if(observed&&current.owner!=s.origin.owner){
     // An authoritative current allocation differs, not a busy/unknown reply.
     gpu_modified_ranges.Subtract(s.address,s.size);
     memory_tracker->UnmarkRegionAsGpuModified(s.address,s.size);break;
    }
    if(observed&&current.mapping.generation!=s.origin.mapping.generation){
     // The same-VA/same-physical insertion was actually replaced. Only this
     // slice is dead; surviving split pieces retain their original generation.
     gpu_modified_ranges.Subtract(from,bytes);
     memory_tracker->UnmarkRegionAsGpuModified(from,bytes);continue;
    }
    const u64 epoch=read_source_epoch.load(std::memory_order_acquire);
    const u64 tick=std::max(s.tick,WriteTicks().Newest(from,bytes));
    // Wait(current) can Flush/PopPendingOperations. A pending fault callback
    // can reenter LayerBind/Maintain and finish this same preserved range.
    // Never keep a Claimed ticket or a borrowed wrapper across those hooks.
    restore();
    if(tick&&!backend.IsFree(tick))backend.Wait(tick);
    const auto download=backend.StagingRequest(bytes,MemoryType::HostCached,0,true);
    // GPU completion alone does not end CPU use: a nested flush can allocate
    // another readback after completion. Use the pool's existing held span.
    struct ReleaseDownload {
     decltype(backend)& runtime;decltype(download)& span;
     ~ReleaseDownload(){if(span.buffer)runtime.StagingFreeDeferred(span);}
    } release_download{backend,download};
    // CopyBuffer may otherwise FlushBarriers after reading its source wrapper.
    // Drain that hook first; its tracker is now empty, so the copy cannot need
    // a second barrier/hook before encoding. This is a cold readback, not Finish.
    backend.FlushBarriers();
    if(!reacquire()){restart=true;break;}
    if(!download.buffer||!download.mapped||download.size<bytes){restore();return false;}
    if(epoch!=read_source_epoch.load(std::memory_order_acquire)||
       tick!=std::max(s.tick,WriteTicks().Newest(from,bytes))){restore();return false;}
    const auto mirror=layer.SnapshotStableMirror(from,bytes);
    if(!mirror||!PreservedWritebackLedger::SameMirror(mirror->source,s.mirror.source)){restore();return false;}
    const auto source=layer_mirrors.find(mirror->source.base);
    if(source==layer_mirrors.end()||source->second.get()!=mirror->source.owner){restore();return false;}
    const Buffer* raw=source->second.get();
    if(raw->Identity()!=s.buffer_identity){restore();return false;}
    const vk::BufferCopy copy{raw->Offset(from),download.offset,bytes};
    const u64 copy_tick=backend.CurrentTick();
    backend.CopyBuffer(raw,download.buffer,std::span{&copy,1});
    restore();
    backend.Wait(copy_tick); // actual private copy completion, no claim or lease
    if(!reacquire()){restart=true;break;}
    if(!backend.IsFreeKnown(copy_tick)){restore();return false;}
    download.buffer->Invalidate(download.offset,bytes);
    if(epoch!=read_source_epoch.load(std::memory_order_acquire)||
       tick!=std::max(s.tick,WriteTicks().Newest(from,bytes))){restore();return false;}
    const auto after=layer.SnapshotStableMirror(from,bytes);
    if(!after||!PreservedWritebackLedger::SameMirror(after->source,s.mirror.source)){restore();return false;}
    const auto after_source=layer_mirrors.find(after->source.base);
    if(after_source==layer_mirrors.end()||after_source->second.get()!=after->source.owner||
       after_source->second->Identity()!=s.buffer_identity){restore();return false;}
    BbRemote::FrozenWriteback batch{};batch.address=from;batch.bytes=bytes;
    batch.tick=copy_tick;batch.content_epoch=epoch;
    if(!BbRemote::AppendWriteback(batch,from,download.mapped,bytes)){restore();return false;}
    const auto result=BbRemote::CommitLiveWriteback(s.origin,batch,
      [&](u64 t){return backend.IsFreeKnown(t);},
      [&](const auto& origin,u64 at,u64 n,auto body){
       return services.protected_lease(origin,at,n,[&](const auto& receipt){
        return layer.WithStableMirror(*after,[&](const auto& span){
         return span.source&&PreservedWritebackLedger::SameMirror(*span.source,s.mirror.source)&&body(receipt);
        });
       });
      },[&](u64 e){return e==read_source_epoch.load(std::memory_order_acquire);});
    if(result==BbRemote::LiveWritebackResult::CommittedUnacknowledged){
     std::fputs("ARM preserved writeback copied but lease completion unacknowledged\n",stderr);
     std::_Exit(BB_ARM_EXIT_HLE_FAULT);
    }
    if(result!=BbRemote::LiveWritebackResult::Committed){restore();return false;}
    // Preserve CPU-newer gaps. Completed slices are never copied by legacy
    // fallback again if another slice later needs a retry.
    gpu_modified_ranges.Subtract(from,bytes);
    memory_tracker->UnmarkRegionAsGpuModified(from,bytes);
   }
   // A nested callback may already have committed/retired the exact entry.
   // Rescan live state; never complete or retry that stale serial, nor recopy it.
   if(restart)continue;
   // Data has committed, but the ledger still owns retirement until every
   // surviving old mapping slice is retired. Busy/failed retirement retries
   // here without copying the already completed bytes again.
   const u64 retired_epoch=read_source_epoch.load(std::memory_order_acquire);
   for(VAddr at=s.address;at<s.address+s.size;){
    uintptr_t begin=0,end=0;int mapped=0;
    if(!services.region(at,&begin,&end,&mapped)||end<=at){restore();return false;}
    const VAddr to=std::min<VAddr>(end,s.address+s.size);const u64 bytes=to-at;
    if(!mapped){at=to;continue;} // authoritative hole; never clear a future owner
    BbPayload::Origin current{};
    if(!services.capture(at,bytes,[&](const auto&r){current={r.owner,r.mapping};return true;})){
     restore();return false;
    }
    if(current.owner!=s.origin.owner||current.mapping.generation!=s.origin.mapping.generation){
     at=to;continue; // new owner/insertion keeps all of its protection reasons
    }
    if((at&4095)||(bytes&4095)||
       !BbPayload::SameMappingForRange(current.mapping,s.origin.mapping,at,bytes)){
     restore();return false;
    }
    const RawLiveBody validate=[&](const BbRemote::LivePayloadReceipt&r){
     return r.owner==s.origin.owner&&r.address==at&&r.size==bytes&&
      BbPayload::SameMappingForRange(r.mapping,s.origin.mapping,at,bytes)&&
      read_source_epoch.load(std::memory_order_acquire)==retired_epoch&&
      !gpu_modified_ranges.Intersects(at,bytes);
    };
    if(!services.retire(s.origin,at,bytes,validate)){restore();return false;}
    // The frontend retired under the same original allocation/VMA/trap lease;
    // only the acknowledged live slice leaves local bookkeeping. No clear RPC.
    {std::scoped_lock trap_lock{layer_read_trap_mutex};layer_read_trapped.Subtract(at,bytes);}
    at=to;
   }
   preserved_writebacks.Complete(*claim);held=false;
  }catch(...){restore();throw;}
 }
 return !preserved_writebacks.Overlaps(address,size);
}
} // namespace VideoCore
