#include "common/debug.h"
#include <cassert>
#include <cstdio>
#include <string>
#include <string_view>

int main() {
    assert(!IsProfilerConnected());
    unsigned evaluations=0;
    [[maybe_unused]] auto hint=[&]() {++evaluations;return std::string(64,'x');};
    TRACE_HINT(hint());
    TRACE_HINT(std::string_view{"borrowed hint"});
    assert(evaluations==0);
    puts("disabled trace hints do not evaluate or allocate their text; string_view caller compiles");
}
