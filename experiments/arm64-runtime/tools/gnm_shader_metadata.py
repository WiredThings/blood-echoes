"""Read-only GNM file-header metadata for locally owned fixed-game shaders.

Independent reader of public OpenGNM shader/shaderbinary structure descriptions:
https://github.com/PS4-OpenGNM/opengnm/tree/4b295ca54c82c83acf308d1c646a2dfa9ae57350
No code bytes, extracted shader files or compiled game assets are emitted.
Register defaults are evidence, not a complete runtime specialization/PSO.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import resource
import struct
import sys
import time
from pathlib import Path

import game_shader_catalog as catalog


class HeaderError(ValueError):
    pass


# Program low/high are file-relative relocations here, never guest addresses.
REGISTERS = {
    1: ("program_low", "program_high", "rsrc1", "rsrc2", "vs_out_config",
        "shader_pos_format", "vs_out_control"),
    2: ("program_low", "program_high", "rsrc1", "rsrc2", "shader_z_format",
        "shader_col_format", "ps_input_ena", "ps_input_addr", "ps_in_control",
        "barycentric_control", "depth_shader_control", "cb_shader_mask"),
    3: ("program_low", "program_high", "rsrc1", "rsrc2", "streamout_config",
        "gs_out_primitive_type", "gs_instance_count"),
    4: ("program_low", "program_high", "rsrc1", "rsrc2", "num_thread_x",
        "num_thread_y", "num_thread_z"),
    5: ("program_low", "program_high", "rsrc1", "rsrc2"),
    6: ("program_low", "program_high", "rsrc1", "rsrc2"),
    7: ("program_low", "program_high", "rsrc1", "rsrc2", "tessellation_param",
        "tessellation_max_level_bits", "tessellation_min_level_bits"),
}
BINARY_TYPES = {1: {1, 6, 8}, 2: {0}, 3: {5}, 4: {4}, 5: {2, 9}, 6: {3}, 7: {7}}
STAGES = {1: "vertex", 2: "fragment", 3: "geometry", 4: "compute",
          5: "export", 6: "local", 7: "hull"}
PREFIX_BUDGET = 4096
MAX_HEADERS = 16


def require(ok, reason):
    if not ok:
        raise HeaderError(reason)


def words(data, start, count):
    require(0 <= start <= len(data) and 0 <= count <= 64 and
            4 * count <= len(data) - start, "register span")
    return struct.unpack_from("<" + "I" * count, data, start)


def properties(registers, compute=False):
    r1, r2 = registers["rsrc1"], registers["rsrc2"]
    result = dict(num_user_data=(r2 >> 1) & 31,
                  num_allocated_vgprs=((r1 & 63) + (0 if compute else 1)) * 4,
                  fp_round_mode32=(r1 >> 12) & 3,
                  fp_round_mode16_64=(r1 >> 14) & 3,
                  fp_denorm_mode32=(r1 >> 16) & 3,
                  fp_denorm_mode16_64=(r1 >> 18) & 3)
    # Follow this runtime's actual BuildRuntimeInfo rules, including its CS rule.
    if compute:
        result.update(tgid_enable=[bool(r2 & (1 << (7 + n))) for n in range(3)],
                      shared_memory_size=((r2 >> 15) & 511) * 512,
                      workgroup_size=[registers["num_thread_" + n] & 65535
                                      for n in "xyz"])
    else:
        result["num_input_vgprs"] = (r1 >> 24) & 3
    return result


def decode_one(data, start, records, by_code=None):
    require(start + 24 <= len(data), "file/common header truncated")
    magic, major, minor, stage, header_dw, aux, gpu, reserved = struct.unpack_from(
        "<4sHH4BI", data, start)
    require(magic == b"Shdr" and (major, minor) == (7, 1), "file-header profile")
    require(stage in REGISTERS and not reserved, "stage/reserved field")
    common = start + 16
    packed, constants, scratch = struct.unpack_from("<IHH", data, common)
    shader_size, usage_count = packed & 0x7fffff, packed >> 24
    names = REGISTERS[stage]
    require(header_dw * 4 >= 8 + len(names) * 4, "header register extent")
    code = common + header_dw * 4
    require(code <= len(data), "header/code span")
    regs = dict(zip(names, words(data, common + 8, len(names))))
    require(common + regs["program_low"] == code and
            regs["program_high"] == 0xffffffff, "unrelocated code reference")
    candidates = by_code.get(code, ()) if by_code is not None else records
    matches = [r for r in candidates if r.get("code_prefix_verified") and
               r.get("code_offset") == code and
               r["binary_type_raw"] in BINARY_TYPES[stage]]
    require(len(matches) == 1, "code/BinaryInfo identity is not unique")
    record = matches[0]
    require(record["code_bytes"] <= shader_size <= len(data) - code,
            "declared shader payload span")
    require(record["binary_info_offset"] + 28 <= code + shader_size,
            "BinaryInfo outside declared payload")
    result = dict(header_offset=start, common_offset=common, stage=STAGES[stage],
                  file_stage=stage, version=[major, minor], aux=aux,
                  target_gpu_modes=gpu, header_bytes=header_dw * 4,
                  shader_payload_bytes=shader_size, uses_srt=bool(packed & 0x800000),
                  embedded_constant_bytes=constants * 16,
                  scratch_dwords_per_thread=scratch, usage_count=usage_count,
                  program_hash=record["program_hash"],
                  binary_type=record["binary_type_raw"], code_offset=code,
                  code_sha256=record["code_sha256"], registers=regs,
                  runtime_property_defaults=properties(regs, stage == 4))
    # Version 7.1 game files contain extra fixed words beyond the public minimal
    # ES/LS/CS/GS/HS structs. Preserve these as uninterpreted values. Their table
    # extents are checked against the independent code relocation/BinaryInfo.
    base = {1: 40, 2: 60, 3: 56, 4: 40, 5: 32, 6: 32, 7: 64}[stage]
    extra_start = {3: 36, 4: 36, 5: 28, 6: 28, 7: 36}.get(stage)
    if extra_start is not None:
        result["uninterpreted_stage_words"] = list(words(
                data, common + extra_start, (base - extra_start) // 4))
    if base is not None:
        require(common + base + usage_count * 4 <= code, "input usage span")
        result["input_usage"] = [list(struct.unpack_from("<4B", data,
                                common + base + i * 4)) for i in range(usage_count)]
        cursor = common + base + usage_count * 4
        if stage in (1, 5, 6):
            count_at = 36 if stage == 1 else 24
            inputs, exports = struct.unpack_from("<BB", data, common + count_at)
            require(cursor + inputs * 4 + exports * 2 <= code, "VS semantic span")
            result["input_semantics"] = [list(struct.unpack_from("<4B", data,
                    cursor + i * 4)) for i in range(inputs)]
            cursor += inputs * 4
            result["export_semantics"] = [list(struct.unpack_from("<2B", data,
                    cursor + i * 2)) for i in range(exports)]
            cursor += exports * 2
        elif stage == 2:
            inputs = data[common + base - 4]
            require(cursor + inputs * 2 <= code, "PS semantic span")
            result["input_semantic_words"] = list(struct.unpack_from(
                    "<" + "H" * inputs, data, cursor))
            cursor += inputs * 2
        result["unparsed_tail_bytes"] = code - cursor
        if stage != 3:
            require((cursor - common + 3) & ~3 == code - common,
                    "semantic table/alignment extent")
    else:
        result["unparsed_tail_bytes"] = code - (common + 8 + len(names) * 4)
    return result


def embedded_gs_copy(data, parent, records):
    """Fixed-title v7.1 GS embeds a VS header immediately after GS usages.

    Its program relocation remains relative to the outer common header. Both
    independently verified OrbShdr identities and exact metadata table extents
    are required; no shader address or pointer is ever followed.
    """
    if parent["file_stage"] != 3:
        return None
    copies = [r for r in records if r.get("code_prefix_verified") and
              r["binary_type_raw"] == 6]
    require(len(copies) == 1, "GS copy identity")
    record = copies[0]
    outer = parent["common_offset"]
    at = outer + 56 + parent["usage_count"] * 4
    require(at + 40 <= parent["code_offset"], "GS embedded VS header span")
    packed, constants, scratch = struct.unpack_from("<IHH", data, at)
    size, usage = packed & 0x7fffff, packed >> 24
    regs = dict(zip(REGISTERS[1], words(data, at + 8, 7)))
    require(outer + regs["program_low"] == record["code_offset"] and
            regs["program_high"] == 0xffffffff, "GS copy relocation")
    require(record["code_bytes"] <= size <= len(data) - record["code_offset"] and
            record["binary_info_offset"] + 28 <= record["code_offset"] + size,
            "GS copy payload span")
    inputs, exports = struct.unpack_from("<BB", data, at + 36)
    cursor = at + 40
    end_usage = cursor + usage * 4
    end_inputs = end_usage + inputs * 4
    end_exports = end_inputs + exports * 2
    require((end_exports - outer + 3) & ~3 == parent["code_offset"] - outer,
            "GS copy table extent")
    return dict(header_offset=None, common_offset=at, stage="vertex", file_stage=1,
                embedded_gs_copy=True, outer_header_offset=parent["header_offset"],
                version=parent["version"], header_bytes=parent["code_offset"] - at,
                shader_payload_bytes=size, uses_srt=bool(packed & 0x800000),
                embedded_constant_bytes=constants * 16,
                scratch_dwords_per_thread=scratch, usage_count=usage,
                program_hash=record["program_hash"], binary_type=6,
                code_offset=record["code_offset"], code_sha256=record["code_sha256"],
                registers=regs, runtime_property_defaults=properties(regs),
                input_usage=[list(struct.unpack_from("<4B", data, cursor + i * 4))
                             for i in range(usage)],
                input_semantics=[list(struct.unpack_from("<4B", data, end_usage + i * 4))
                                 for i in range(inputs)],
                export_semantics=[list(struct.unpack_from("<2B", data, end_inputs + i * 2))
                                  for i in range(exports)],
                unparsed_tail_bytes=parent["code_offset"] - end_exports)


def shader_headers(data, records=None, *, embedded=False):
    require(len(data) <= catalog.LIMIT, "resource byte budget")
    records = catalog.orb_metadata(data)["records"] if records is None else records
    verified = [r for r in records if r.get("code_prefix_verified")]
    first_code = min((r["code_offset"] for r in verified), default=0)
    if embedded:
        require(bytes(memoryview(data)[:4]) == b"\x7fELF", "reconstructed ELF required")
        prefix = data if isinstance(data, (bytes, bytearray)) else bytes(data)
    else:
        prefix = bytes(memoryview(data)[:min(first_code, PREFIX_BUDGET)])
    by_code = collections.defaultdict(list)
    for record in verified:
        by_code[record["code_offset"]].append(record)
    accepted, rejected, cursor = [], [], 0
    while True:
        at = prefix.find(b"Shdr", cursor)
        if at < 0:
            break
        require(len(accepted) + len(rejected) < (65536 if embedded else MAX_HEADERS),
                "header candidate budget")
        cursor = at + 4
        if embedded and (at + 8 > len(data) or
                         struct.unpack_from("<HH", data, at + 4) != (7, 1)):
            # Ordinary strings and OrbShdr trailers are not file-header claims.
            continue
        try:
            primary = decode_one(data, at, verified, by_code)
            accepted.append(primary)
            copy = embedded_gs_copy(data, primary, verified)
            if copy is not None:
                # The parent's trailing metadata is this independently checked
                # copy header, not unparsed GS data.
                primary["embedded_copy_header_bytes"] = primary["unparsed_tail_bytes"]
                primary["unparsed_tail_bytes"] = 0
                accepted.append(copy)
        except (HeaderError, struct.error) as error:
            rejected.append(dict(offset=at, reason=str(error)))
    covered = {r["code_offset"] for r in accepted}
    return dict(headers=accepted, rejected=rejected,
                unassociated_programs=[dict(program_hash=r["program_hash"],
                    binary_type=r["binary_type_raw"], code_offset=r["code_offset"])
                    for r in verified if r["code_offset"] not in covered])


def checked_elf(data, name):
    """Bound reconstruction before invoking the original SELF loader."""
    require(len(data) <= catalog.LIMIT, "module input budget")
    magic = bytes(memoryview(data)[:4])
    base = 0
    if magic == b"O\x15=\x1d":
        segments = catalog.unpack("<H", data, 24)[0]
        require(segments < 256, "SELF segment count budget")
        base = 32 + segments * 32
    else:
        require(magic == b"\x7fELF", "ELF/SELF magic")
    header = catalog.unpack("<16sHHIQQQIHHHHHH", data, base)
    require(header[0][:7] == b"\x7fELF\x02\x01\x01" and header[2] == 62,
            "ELF architecture")
    require(header[9] == 56 and 0 < header[10] < 256, "ELF program header shape")
    end = header[5] + header[10] * 56
    require(end <= catalog.LIMIT, "ELF header budget")
    for i in range(header[10]):
        ph = catalog.unpack("<IIQQQQQQ", data, base + header[5] + i * 56)
        require(ph[2] <= catalog.LIMIT and ph[5] <= catalog.LIMIT - ph[2],
                "ELF reconstruction budget")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                         "vendor/blood_echoes/loader"))
    from prepare import parse_self
    result = parse_self(data, name)
    require(len(result[0]) <= catalog.LIMIT, "ELF output budget")
    return result


def write_row(stream, row):
    # Avoid a second whole-module metadata JSON string alongside the records.
    for chunk in json.JSONEncoder(separators=(",", ":")).iterencode(row):
        stream.write(chunk)
    stream.write("\n")


def census(root, prior, output, *, products=None, source_files=None):
    started = time.monotonic()
    require(root.is_absolute() and root == root.resolve(), "canonical absolute game root")
    known = json.loads(prior.read_text())
    require(Path(known["game_root"]) == root, "prior catalog game root")
    require(catalog.game_identity((root / "sce_sys/param.sfo").read_bytes()) ==
            known["game_identity"], "game identity changed")
    output.mkdir(parents=True, exist_ok=False)
    counts, stages, tails, missing = (collections.Counter() for _ in range(4))
    package_receipts = []
    with (output / "headers.jsonl").open("w") as stream:
        for package in known["packages"]:
            if package["kind"] not in ("shader", "embedded") or package["debug"]:
                continue
            relative = Path(package["path"])
            require(not relative.is_absolute() and ".." not in relative.parts, "package path")
            path = root / relative
            require(not path.is_symlink() and path.resolve().is_relative_to(root), "package symlink")
            require(path.stat().st_size <= catalog.LIMIT, "package byte budget")
            from resource_parse_products import identity as metadata_identity
            proof = dict(kind='gnm-package-headers-v1', package_metadata_sha256=metadata_identity(package))
            if source_files is None:
                require(catalog_identity(root/package['path'])==package['sha256'], 'package changed before metadata reuse')
            cached = products.get(proof) if products is not None else None
            if cached is not None:
                require(set(cached)=={'rows','counts','stages','tails','missing','receipt'}, 'GNM product fields')
                for row in cached['rows']:write_row(stream,row)
                for target,name in [(counts,'counts'),(stages,'stages'),(tails,'tails'),(missing,'missing')]:target.update(cached[name])
                package_receipts.append(cached['receipt'])
                continue
            old_counts=[x.copy() for x in (counts,stages,tails,missing)]
            captured=[];captured_bytes=0
            def emit(row):
                nonlocal captured,captured_bytes
                write_row(stream,row)
                if captured is not None:
                    captured_bytes+=len(json.dumps(row,separators=(',',':')).encode())
                    if captured_bytes<=3*1024*1024:captured.append(row)
                    else:captured=None
            def remember():
                if products is None or captured is None:return
                deltas=[dict(now-old) for now,old in zip((counts,stages,tails,missing),old_counts)]
                products.put(proof,dict(rows=captured,counts=deltas[0],stages=deltas[1],tails=deltas[2],missing=deltas[3],receipt=package_receipts[-1]))
            raw = path.read_bytes()
            require(hashlib.sha256(raw).hexdigest() == package["sha256"], "package changed")
            if package["kind"] == "embedded":
                data, _header, _ph, _segments, absent = checked_elf(raw, str(relative))
                del raw
                result = shader_headers(data, embedded=True)
                emit(dict(package=str(relative), entry=None,
                    elf_sha256=hashlib.sha256(data).hexdigest(), **result))
                counts["embedded_modules"] += 1
                counts["accepted_headers"] += len(result["headers"])
                counts["rejected_candidates"] += len(result["rejected"])
                counts["unassociated_programs"] += len(result["unassociated_programs"])
                for header in result["headers"]:
                    stages[header["stage"]] += 1
                    tails[f'{header["stage"]}:{header["unparsed_tail_bytes"]}'] += 1
                require(catalog_identity(path) == package["sha256"], "module changed during scan")
                package_receipts.append(dict(path=str(relative), sha256=package["sha256"],
                                             missing_optional_program_headers=absent))
                del data, result
                remember()
                continue
            data = catalog.dcx(raw)
            del raw
            binder = catalog.bnd4(data)
            require(len(binder["entries"]) == package["entry_count"], "entry count changed")
            for entry in binder["entries"]:
                payload = catalog.entry_data(data, entry)
                result = shader_headers(payload)
                emit(dict(package=str(relative), entry=entry["name"],
                    entry_sha256=hashlib.sha256(payload).hexdigest(), **result))
                counts["entries"] += 1
                counts["accepted_headers"] += len(result["headers"])
                counts["rejected_candidates"] += len(result["rejected"])
                counts["unassociated_programs"] += len(result["unassociated_programs"])
                if not result["headers"]:
                    missing[Path(entry["name"]).suffix] += 1
                for header in result["headers"]:
                    stages[header["stage"]] += 1
                    tails[f'{header["stage"]}:{header["unparsed_tail_bytes"]}'] += 1
                del payload
            require(catalog_identity(path) == package["sha256"], "package changed during scan")
            package_receipts.append(dict(path=str(relative), sha256=package["sha256"]))
            del data, binder
            remember()
    summary = dict(schema=1, counts=dict(counts), stages=dict(stages),
                   unparsed_tail_sizes=dict(tails), entries_without_header=dict(missing),
                   package_receipts=package_receipts,
                   elapsed_seconds=time.monotonic() - started,
                   peak_rss_native_units=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                   qualification="File register defaults and semantic metadata only; no full runtime/PSO coverage claim. No game payload or compiled output emitted.")
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def catalog_identity(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-root", type=Path, required=True)
    parser.add_argument("--prior-catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True,
                        help="New private local directory; do not publish game metadata")
    args = parser.parse_args()
    print(json.dumps(census(args.game_root, args.prior_catalog, args.output), indent=2))


if __name__ == "__main__":
    main()
