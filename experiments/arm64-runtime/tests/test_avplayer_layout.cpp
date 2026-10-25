// SPDX-License-Identifier: GPL-2.0-or-later
// Check the same canonical structures under both guest and host Clang targets.
#include "core/libraries/avplayer/avplayer.h"
#include <cstddef>
using namespace Libraries::AvPlayer;
static_assert(sizeof(void*)==8);
static_assert(sizeof(AvPlayerInitData)==120);
static_assert(offsetof(AvPlayerInitData,memory_replacement)==0);
static_assert(offsetof(AvPlayerInitData,file_replacement)==40);
static_assert(offsetof(AvPlayerInitData,event_replacement)==80);
static_assert(offsetof(AvPlayerInitData,num_output_video_framebuffers)==104);
static_assert(offsetof(AvPlayerInitData,auto_start)==108);
static_assert(offsetof(AvPlayerInitData,default_language)==112);
static_assert(sizeof(AvPlayerFrameInfo)==40);
static_assert(sizeof(AvPlayerFrameInfoEx)==104);
static_assert(offsetof(AvPlayerFrameInfo,timestamp)==16);
static_assert(offsetof(AvPlayerFrameInfo,details)==24);
static_assert(offsetof(AvPlayerFrameInfoEx,details)==24);
static_assert(sizeof(AvPlayerStreamInfo)==40);
static_assert(sizeof(AvPlayerPostInitData)==132);
static_assert(sizeof(AvPlayerMemAllocator)==40);
static_assert(sizeof(AvPlayerFileReplacement)==40);
static_assert(sizeof(AvPlayerEventReplacement)==16);
