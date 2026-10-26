// SPDX-License-Identifier: GPL-2.0-or-later
// Exercise the same WindowSDL/Cocoa constructor used by bb-probe-metal, without guest RAM.
#import <AppKit/AppKit.h>
#include <SDL3/SDL.h>
#include <cassert>
#include <cstdlib>
#include <cstdio>
#include <thread>
#include "sdl_window.h"
#include "macos_app_icon.h"

int main(int argc, char** argv) {
    assert(argc == 4);
    setenv("BB_GAME_DIR", argv[1], 1); setenv("BB_FULLSCREEN", "0", 1);
    const bool missing = std::string(argv[3]) == "missing";
    @autoreleasepool {
        assert(SDL_Init(SDL_INIT_VIDEO));
        NSImage* initial = NSApp.applicationIconImage;
        NSData* initialData = [initial.TIFFRepresentation copy];
        {
            Frontend::WindowSDL window(64, 64, "bb-probe-metal icon smoke (no game)");
            int count = 0; SDL_Window** windows = SDL_GetWindows(&count);
            for (int i=0;i<count;++i) SDL_HideWindow(windows[i]);
            SDL_free(windows);
            if (missing) {
                assert(!BbPort::SetGameDockIcon());
                assert([NSApp.applicationIconImage.TIFFRepresentation isEqualToData:initialData]);
            } else {
                NSImage* icon = NSApp.applicationIconImage;
                assert(icon);
                NSData* changedData = [icon.TIFFRepresentation copy];
                std::printf("Icon data initial=%lu current=%lu\n", (unsigned long)initialData.length, (unsigned long)changedData.length); std::fflush(stdout);
                assert(![changedData isEqualToData:initialData]);
                std::printf("Cocoa Dock logical size: %.0fx%.0f points\n", icon.size.width, icon.size.height);
                NSBitmapImageRep* rep = [NSBitmapImageRep imageRepWithData:icon.TIFFRepresentation];
                // AppKit mutates its cached icon object and snapshots at display scale.
                assert(rep.pixelsWide >= 128 && rep.pixelsWide <= 1024 && rep.pixelsHigh == rep.pixelsWide);
                assert([rep colorAtX:0 y:0].alphaComponent < 0.01);
                assert([rep colorAtX:rep.pixelsWide*96/1024 y:rep.pixelsHigh*96/1024].alphaComponent < 0.1);
                assert([rep colorAtX:rep.pixelsWide/2 y:rep.pixelsHigh/2].alphaComponent > 0.99);
                assert(BbPort::SetGameDockIcon() && [NSApp.applicationIconImage.TIFFRepresentation isEqualToData:changedData]);
                bool wrong_thread = true;
                std::thread worker([&] { wrong_thread = BbPort::SetGameDockIcon(); }); worker.join();
                assert(!wrong_thread && [NSApp.applicationIconImage.TIFFRepresentation isEqualToData:changedData]);
                NSData* data = [rep representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
                assert([data writeToFile:[NSString stringWithUTF8String:argv[2]] atomically:YES]);
            }
        }
        SDL_Quit();
    }
    std::puts(missing ? "Missing-art fallback passed" : "Game-process Cocoa icon, alpha/padding, one-shot and thread checks passed");
}
