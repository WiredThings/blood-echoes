"""Authored format records only; never load or embed game shader bytes."""
import copy
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import gnm_shader_metadata as g


def fixture(stage=1, binary_type=None, wrapper=36):
    kind = min(g.BINARY_TYPES[stage]) if binary_type is None else binary_type
    base = {1: 40, 2: 60, 3: 56, 4: 40, 5: 32, 6: 32, 7: 64}[stage]
    common = wrapper + 16
    usage = 2
    extra = 8 + 2 if stage in (1, 5, 6) else 2 if stage == 2 else 0
    header = (base + usage * 4 + extra + 3) & ~3
    code = common + header
    data = bytearray(code + 96)
    struct.pack_into("<4sHH4BI", data, wrapper, b"Shdr", 7, 1, stage, header // 4, 0, 1, 0)
    struct.pack_into("<IHH", data, common, 96 | usage << 24, 0, 0)
    regs = [header, 0xffffffff, 0x30c0083, 0x20] + [0] * (len(g.REGISTERS[stage]) - 4)
    if stage == 4:
        regs[4:] = [64, 2, 1]
    struct.pack_into("<" + "I" * len(regs), data, common + 8, *regs)
    if stage in (1, 5, 6):
        struct.pack_into("<BB", data, common + (36 if stage == 1 else 24), 2, 1)
    elif stage == 2:
        data[common + 56] = 1
    struct.pack_into("<4B4B", data, common + base, 2, 0, 0, 0, 2, 1, 4, 0)
    struct.pack_into("<II", data, code, 0xbeeb03ff, 7)
    struct.pack_into("<7sBI4BQI", data, code + 64, b"OrbShdr", 1,
                     64 << 8 | kind << 2, 0, usage, 0, 0, 0x1234, 0)
    return data


def gs_fixture():
    data = fixture(3, wrapper=0)
    common, base = 16, 56
    copy_common = common + base + 8
    # GS header includes its usage table and a complete embedded copy VS header.
    end_header = copy_common + 40 + 4 + 2 * 2
    first_code = end_header
    copy_code = first_code + 96
    data = data[:copy_common] + bytearray(copy_code + 96 - copy_common)
    data[9] = (end_header - common) // 4
    struct.pack_into("<I", data, common + 8, first_code - common)
    struct.pack_into("<IHH", data, copy_common, 96 | 1 << 24, 0, 0)
    struct.pack_into("<7I", data, copy_common + 8, copy_code - common,
                     0xffffffff, 0x84, 4, 6, 0x44, 0)
    struct.pack_into("<BB", data, copy_common + 36, 0, 2)
    struct.pack_into("<4B", data, copy_common + 40, 0x1a, 0, 0, 0)
    for code, kind, identity in [(first_code, 5, 0x1234), (copy_code, 6, 0x5678)]:
        struct.pack_into("<II", data, code, 0xbeeb03ff, 7)
        struct.pack_into("<7sBI4BQI", data, code + 64, b"OrbShdr", 1,
                         64 << 8 | kind << 2, 0, 0, 0, 0, identity, 0)
    return data


class Tests(unittest.TestCase):
    def test_authored_stages_and_relocations(self):
        for stage in (1, 2, 4, 5, 6, 7):
            with self.subTest(stage=stage):
                result = g.shader_headers(fixture(stage))
                self.assertEqual(result["rejected"], [])
                self.assertEqual(result["unassociated_programs"], [])
                self.assertEqual(result["headers"][0]["file_stage"], stage)
                self.assertLessEqual(result["headers"][0]["unparsed_tail_bytes"], 2)

    def test_properties_follow_runtime_rule(self):
        p = g.properties(dict(rsrc1=0x30c0083, rsrc2=0x20))
        self.assertEqual((p["num_user_data"], p["num_input_vgprs"], p["num_allocated_vgprs"]), (16, 3, 16))
        p = g.shader_headers(fixture(4))["headers"][0]["runtime_property_defaults"]
        self.assertEqual(p["workgroup_size"], [64, 2, 1])
        self.assertEqual(p["num_allocated_vgprs"], 12)

    def test_embedded_alignment_is_relative_to_header(self):
        for wrapper in (0, 1, 2, 38):
            for stage in (1, 2, 4, 5, 6, 7):
                result = g.shader_headers(fixture(stage, wrapper=wrapper))
                self.assertEqual(result["rejected"], [], (wrapper, stage))
                self.assertEqual(len(result["headers"]), 1)

    def test_embedded_scan_uses_actual_elf_scope(self):
        data = bytearray(5000) + fixture(wrapper=38)
        data[:4] = b"\x7fELF"
        self.assertEqual(len(g.shader_headers(data, embedded=True)["headers"]), 1)
        self.assertFalse(g.shader_headers(data)["headers"])
        with self.assertRaises(g.HeaderError):
            g.shader_headers(fixture(), embedded=True)

    def test_elf_reconstruction_budget_precedes_original_loader(self):
        data = bytearray(128)
        ident = b"\x7fELF\x02\x01\x01" + bytes(9)
        struct.pack_into("<16sHHIQQQIHHHHHH", data, 0, ident, 2, 62, 1,
                         0, 64, 0, 0, 64, 56, 1, 0, 0, 0)
        struct.pack_into("<IIQQQQQQ", data, 64, 1, 4, 0, 0, 0, len(data), len(data), 1)
        self.assertEqual(len(g.checked_elf(data, "authored")[0]), len(data))
        struct.pack_into("<Q", data, 64 + 8, g.catalog.LIMIT + 1)
        with self.assertRaisesRegex(g.HeaderError, "reconstruction budget"):
            g.checked_elf(data, "authored")

    def test_default_comparison_preserves_modified_and_ambiguous_cases(self):
        import json
        from compare_gnm_runtime_metadata import compare
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            headers = root / "headers.jsonl"
            rows = [dict(program_hash="1234", stage="vertex", code_sha256=code,
                         runtime_property_defaults=dict(num_user_data=16,
                         num_input_vgprs=0, num_allocated_vgprs=16)) for code in ("a", "b")]
            headers.write_text(json.dumps(dict(headers=rows)) + "\n")
            (root / "cache").mkdir()
            meta = dict(program_hash="1234", stage="vertex", serialized_spec_sha256="spec",
                        runtime_properties=dict(num_user_data=16, num_input_vgprs=3,
                                                num_allocated_vgprs=16))
            (root / "cache/decoded.json").write_text(json.dumps(dict(metadata=[meta, meta])))
            result = compare(headers, root)
            self.assertEqual(result["counts"]["defaults_require_modification"], 2)
            self.assertEqual(result["counts"]["multiple_static_code_digests"], 2)
            self.assertEqual(result["distinct_matched_serialized_specs"], 1)
            self.assertEqual(result["mismatching_fields"], {"vertex.num_input_vgprs": 2})

    def test_embedded_copy_uses_outer_relocation(self):
        result = g.shader_headers(gs_fixture())
        self.assertEqual(result["rejected"], [])
        self.assertEqual(result["unassociated_programs"], [])
        self.assertEqual([h["binary_type"] for h in result["headers"]], [5, 6])
        self.assertTrue(result["headers"][1]["embedded_gs_copy"])

    def test_truncated_inputs_never_accept_incomplete_payload(self):
        data = fixture()
        for end in range(len(data)):
            self.assertFalse(g.shader_headers(data[:end])["headers"], end)

    def test_reject_relocated_or_wrong_code_pointer(self):
        for offset, value in [(60, 0), (64, 0)]:
            data = fixture()
            struct.pack_into("<I", data, offset, value)
            result = g.shader_headers(data)
            self.assertFalse(result["headers"])
            self.assertTrue(result["rejected"])

    def test_stage_mismatch_rejected(self):
        data = fixture(1, binary_type=0)
        self.assertIn("identity", g.shader_headers(data)["rejected"][0]["reason"])

    def test_duplicate_binary_identity_rejected(self):
        data = fixture()
        records = g.catalog.orb_metadata(data)["records"]
        self.assertFalse(g.shader_headers(data, records + copy.deepcopy(records))["headers"])

    def test_oversized_usage_and_semantics_rejected(self):
        for offset in (55, 88):
            data = fixture()
            data[offset] = 255
            self.assertFalse(g.shader_headers(data)["headers"])

    def test_bad_payload_extent_rejected(self):
        data = fixture()
        struct.pack_into("<I", data, 52, 0x7fffff | 2 << 24)
        self.assertFalse(g.shader_headers(data)["headers"])

    def test_mismatched_gs_copy_keeps_explicit_unassociated_record(self):
        data = gs_fixture()
        struct.pack_into("<I", data, 16 + 56 + 8 + 8, 0)
        result = g.shader_headers(data)
        self.assertEqual(len(result["headers"]), 1)
        self.assertEqual(len(result["unassociated_programs"]), 1)
        self.assertTrue(result["rejected"])


if __name__ == "__main__":
    unittest.main()
