# Thread and context contract review

Structural controls only: no signal/context implementation or native restore call.
This source references the existing prepared Aether/shadPS4 exception.h guest layout
and prototype guest_module_info.h; it does not assert a fully verified PS4 ABI.
See docs/thread-signal-contracts.zh-CN.md for actual dual-architecture results and
the unresolved trampoline, XSAVE and FEX control-transfer gates.
No game bytes or callsite ledger belongs here.
