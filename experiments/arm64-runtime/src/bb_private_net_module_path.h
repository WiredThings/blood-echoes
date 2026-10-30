// Private ARM prototype only; called after Shared QA session admission.
// Preserve the existing build-tree module path and admit the unchanged launcher's
// bundled module only beside this actual executable. No separate frontend path.
#pragma once
#include <stddef.h>
#include <string.h>
#if defined(__APPLE__)
extern int bb_bundled_net_module_path(const char*,const char*) __attribute__((weak_import));
#else
extern int bb_bundled_net_module_path(const char*,const char*) __attribute__((weak));
#endif
static inline int bb_arm_private_net_module_path(const char* canonical,const char* executable){
    if(!canonical||!executable)return 0;
    if(bb_bundled_net_module_path){const int formal=bb_bundled_net_module_path(canonical,executable);if(formal>=0)return formal;}
    static const char build[]=BB_PROTOTYPE_ROOT "/build/";
    if(!strncmp(canonical,build,sizeof(build)-1))return 1;
    static const char* suffixes[]={"/Contents/Resources/bbport/bin/bb-probe-metal",
        "/Contents/Resources/bbport/bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal",
        "/Contents/Resources/bbport/bin/Bloodborne bbcpu Game.app/Contents/MacOS/bb-probe-metal"};
    static const char module[]="/Contents/Resources/bbport/bin/gpu-net/libbbnet.dylib";
    const size_t n=strlen(executable),m=strlen(canonical);
    for(size_t i=0;i<sizeof(suffixes)/sizeof(suffixes[0]);++i){
        const size_t length=strlen(suffixes[i]);
        if(n<length||strcmp(executable+n-length,suffixes[i]))continue;
        const size_t parent=n-length;
        return m==parent+sizeof(module)-1&&!memcmp(canonical,executable,parent)&&!strcmp(canonical+parent,module);
    }
    return 0;
}
