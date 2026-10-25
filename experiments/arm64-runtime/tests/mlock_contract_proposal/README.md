# Mlock contract proposal controls

This executable model and authored controls are not installed runtime code.
The model has an explicit accounting/failure backend; it cannot satisfy a guest import.
Its finite limits are a candidate policy, not measured game requirements.

Run `python3 tests/mlock_contract_proposal/test_bounded_mlock_model.py`.
The native C microcontrol deliberately pins at most one 16 KiB mapping and cleans it up.
The fixed-boot analyzer reads only explicitly supplied local game inputs and emits metadata.
No game inputs, disassembly, compiled control, or output ledger belongs in this directory.
See `docs/mlock-contract-review.zh-CN.md` in the source snapshot for the integration gates.
