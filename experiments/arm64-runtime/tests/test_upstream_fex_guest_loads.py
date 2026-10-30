"""Execute FEX's real IR generator and compiled dead-code predicate for #6074.

No guest/game execution is claimed: the generated HasSideEffects table is used
by the verbatim production EliminateDeadCode method with minimal node/emitter
containers. CPU-cache identity tests use the production builder and authored
archive bytes in a temporary directory; no existing cache is opened or changed.
"""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FEX = ROOT / "vendor/aether/runtime/sources/fexcore-darwin/FEXCore"
IR = FEX / "Source/Interface/IR/IR.json"
GENERATOR = FEX / "Scripts/json_ir_generator.py"
DCE = FEX / "Source/Interface/IR/Passes/RedundantFlagCalculationElimination.cpp"
CHANGED = ("LoadMem", "LoadMemTSO", "VLoadVectorMasked", "VLoadVectorGatherMasked",
           "VLoadVectorGatherMaskedQPS", "VLoadVectorElement")
LOADS = CHANGED + ("VLoadNonTemporal", "LoadMemPair")

PREFIX = r'''
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <string>
namespace FEXCore::IR {
#define IROP_ENUM
#include "ir.inc"
#define IROP_HASSIDEEFFECTS_IMPL
#include "ir.inc"
struct Node { unsigned uses; bool removed{}; unsigned GetUses() const { return uses; } };
using Ref = Node*;
struct IROp_Header { IROps Op; };
struct IREmitter { void Remove(Ref node) { node->removed = true; } };
class DeadFlagCalculationEliminination {
public:
    bool EliminateDeadCode(IREmitter* IREmit, Ref CodeNode, IROp_Header* IROp);
};
'''
SUFFIX = r'''
}
int main(int argc, char** argv) {
    using namespace FEXCore::IR;
    assert(argc == 2);
    const std::string selected = argv[1];
    struct Candidate { const char* name; IROps op; };
    const Candidate candidates[] = {
        {"LoadMem", OP_LOADMEM}, {"LoadMemTSO", OP_LOADMEMTSO},
        {"VLoadVectorMasked", OP_VLOADVECTORMASKED},
        {"VLoadVectorGatherMasked", OP_VLOADVECTORGATHERMASKED},
        {"VLoadVectorGatherMaskedQPS", OP_VLOADVECTORGATHERMASKEDQPS},
        {"VLoadVectorElement", OP_VLOADVECTORELEMENT},
        {"VLoadNonTemporal", OP_VLOADNONTEMPORAL}, {"LoadMemPair", OP_LOADMEMPAIR},
    };
    DeadFlagCalculationEliminination pass;
    IREmitter emitter;
    for (const auto& candidate : candidates) {
        if (selected != candidate.name) continue;
        IROp_Header operation{candidate.op};
        for (unsigned uses : {0u,1u}) {
            Node node{uses};
            if (!HasSideEffects(operation.Op) || pass.EliminateDeadCode(&emitter, &node, &operation) || node.removed) {
                std::fprintf(stderr, "lost guest load %s uses=%u\n", candidate.name, uses);
                return 1;
            }
        }
        return 0;
    }
    if (selected == "pure-dead") {
        Node node{0}; IROp_Header operation{OP_ADD};
        assert(!HasSideEffects(operation.Op));
        assert(pass.EliminateDeadCode(&emitter, &node, &operation));
        assert(node.removed);
        return 0;
    }
    if (selected == "pure-used") {
        Node node{1}; IROp_Header operation{OP_ADD};
        assert(!pass.EliminateDeadCode(&emitter, &node, &operation));
        assert(!node.removed);
        return 0;
    }
    return 2;
}
'''


def entries(document):
    for category in document["Ops"].values():
        for signature, definition in category.items():
            yield signature.split(" = ")[-1].split(" ", 1)[0], definition


def method_body():
    text = DCE.read_text()
    return ("bool DeadFlagCalculationEliminination::EliminateDeadCode("
            + text.split("bool DeadFlagCalculationEliminination::EliminateDeadCode(", 1)[1]
            .split("\nCondClass DeadFlagCalculationEliminination::X86ToArmFloatCond", 1)[0])


class GuestLoadEffects(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="be-upstream-fex-loads-", dir="/private/tmp")
        cls.directory = Path(cls.tmp.name)
        cls.compiler = shutil.which("clang++")
        if not cls.compiler:
            raise RuntimeError("clang++ is required")
        cls.document = json.loads(IR.read_text())
        cls.native, cls.generated = cls.build("current", cls.document)
        old = json.loads(IR.read_text())
        for name, definition in entries(old):
            if name in CHANGED:
                definition.pop("HasSideEffects", None)
        cls.old, cls.old_generated = cls.build("pre-6074", old)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def build(cls, name, document):
        work = cls.directory / name
        work.mkdir()
        source = work / "IR.json"
        source.write_text(json.dumps(document))
        generated, dispatcher = work / "ir.inc", work / "dispatcher.inc"
        subprocess.run([sys.executable, GENERATOR, source, generated, dispatcher],
                       capture_output=True, text=True, check=True, timeout=15)
        program = work / "control.cpp"
        program.write_text(PREFIX + method_body() + SUFFIX)
        binary = work / "control"
        result = subprocess.run([cls.compiler, "-std=c++23", "-O2", "-Wall", "-Wextra", "-Werror",
                                 "-I", str(work), str(program), "-o", str(binary)],
                                capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return binary, generated

    def run_control(self, executable, mode):
        return subprocess.run([executable, mode], capture_output=True, text=True, timeout=5)

    def test_six_new_loads_retained_with_zero_or_nonzero_uses(self):
        for name in CHANGED:
            with self.subTest(name=name):
                result = self.run_control(self.native, name)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_non_temporal_and_paired_load_protection_preserved(self):
        for name in ("VLoadNonTemporal", "LoadMemPair"):
            with self.subTest(name=name):
                self.assertEqual(self.run_control(self.native, name).returncode, 0)
                self.assertEqual(self.run_control(self.old, name).returncode, 0)

    def test_pure_unused_arithmetic_is_still_eliminated(self):
        result = self.run_control(self.native, "pure-dead")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_used_arithmetic_is_still_preserved(self):
        result = self.run_control(self.native, "pure-used")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pre_fix_generator_demonstrates_all_six_lost_loads(self):
        for name in CHANGED:
            with self.subTest(name=name):
                result = self.run_control(self.old, name)
                self.assertEqual(result.returncode, 1)
                self.assertIn("lost guest load " + name + " uses=0", result.stderr)

    def test_generated_enum_dispatch_and_other_tables_stay_identical(self):
        current, old = self.generated.read_text(), self.old_generated.read_text()
        def without_effects(text):
            before, after = text.split("#ifdef IROP_HASSIDEEFFECTS_IMPL", 1)
            _, after = after.split("#undef IROP_HASSIDEEFFECTS_IMPL", 1)
            return before + after
        self.assertEqual(without_effects(current), without_effects(old))
        self.assertEqual((self.generated.parent / "dispatcher.inc").read_bytes(),
                         (self.old_generated.parent / "dispatcher.inc").read_bytes())

    def test_existing_cpu_archive_identity_changes_without_deleting_old_cache(self):
        # The actual identity tool requires Python >=3.11 (hashlib.file_digest).
        self.assertTrue(hasattr(hashlib, "file_digest"), "run with Python 3.11 or newer")
        work = self.directory / "cache-identity"
        build, renderer = work / "build", work / "renderer"
        build.mkdir(parents=True)
        renderer.mkdir()
        fex, bridge = build / "fex.a", build / "bridge.a"
        fex.write_bytes(b"authored old FEX archive identity input\n")
        bridge.write_bytes(b"authored unchanged bridge identity input\n")
        old_cache = build / "old-cache.bin"
        old_cache.write_bytes(b"authored preserved CPU cache fixture\n")
        preserved = old_cache.read_bytes()
        manifest = build / "identity.json"
        command = [sys.executable, ROOT / "tools/make_cpu_cache_build.py", "--source-root", ROOT,
                   "--build-root", build, "--renderer-root", renderer,
                   "--runtime-part", "fex=" + str(fex), "--bridge-part", "native=" + str(bridge),
                   "--header", build / "identity.h", "--manifest", manifest]
        def identity():
            subprocess.run(command, capture_output=True, text=True, check=True, timeout=10)
            return json.loads(manifest.read_text())
        before = identity()
        self.assertEqual(before, identity())
        fex.write_bytes(b"authored changed FEX archive identity input\n")
        after = identity()
        self.assertNotEqual(before["runtime_build"], after["runtime_build"])
        self.assertEqual(before["bridge_build"], after["bridge_build"])
        self.assertEqual(after["runtime_parts"]["fex"], hashlib.sha256(fex.read_bytes()).hexdigest())
        self.assertEqual(old_cache.read_bytes(), preserved)


if __name__ == "__main__":
    unittest.main(verbosity=2)
