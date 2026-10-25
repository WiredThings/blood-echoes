# Static metadata format references

- [SoulsFormats Binder and FLVER source definitions](https://github.com/soulsmods/SoulsFormats/tree/master/SoulsFormats/Formats): field layout references for the independent bounded readers.
- [OpenGNM pinned shader-binary definitions](https://github.com/PS4-OpenGNM/opengnm/blob/4b295ca54c82c83acf308d1c646a2dfa9ae57350/include/gnm_shaderbinary.h) and [stage registers](https://github.com/PS4-OpenGNM/opengnm/blob/4b295ca54c82c83acf308d1c646a2dfa9ae57350/include/gnm_shader.h): public minimal structures, checked against actual fixed-game v7.1 extents rather than assumed complete.
- Vendored `video_core/amdgpu/regs_shader.h`, `shader_recompiler/runtime_info.h`, and the original `vendor/blood_echoes/loader/prepare.py`: runtime contracts retained by this project.

See `docs/full-game-static-catalog.zh-CN.md` and `docs/static-shader-register-inputs.zh-CN.md` for measured coverage, unresolved fields and local-only output boundaries. These readers emit metadata; they do not decrypt or distribute game assets.
