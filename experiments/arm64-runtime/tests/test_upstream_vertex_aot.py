"""Invariant recipe admission/identity regressions. Authored data; never starts a game."""
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import local_metal_builder as builder
import compilation_domains as domains
import test_local_metal_builder as fixtures


class InvariantRunner(fixtures.FakeRunner):
    @staticmethod
    def assert_metal(argv):
        original = list(argv)
        if '-fpreserve-invariance' in original:
            original.remove('-fpreserve-invariance')
        fixtures.FakeRunner.assert_metal(original)


class UpstreamVertexAotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.top = Path(tempfile.mkdtemp(prefix='upstream-vertex-aot-', dir=ROOT / 'state'))

    setUp = fixtures.BuilderTests.setUp
    export = fixtures.BuilderTests.export
    run_builder = fixtures.BuilderTests.run_builder
    artifact = fixtures.BuilderTests.artifact
    select_cohort = fixtures.BuilderTests.select_cohort

    def invariant(self, **changes):
        fields = dict(schema=3, recipe=builder.INVARIANT_RECIPE, preserve_invariance=True)
        fields.update(changes)
        return self.export(**fields)

    def test_legacy_key_is_byte_identical(self):
        key, request = self.export()
        fields = [request[k] for k in ('recipe', 'cohort', 'source_sha256', 'entry',
                                       'language', 'math_mode', 'math_fp32')]
        old = hashlib.sha256(b''.join(str(len(v.encode())).encode() + b':' + v.encode()
                                      for v in fields)).hexdigest()
        self.assertEqual(key, old)
        self.assertEqual(builder.load_request(self.requests, key, self.cohort)[0], request)

    def test_same_source_options_distinguish_invariant_recipe_and_product(self):
        oldkey, old = self.export()
        key, request = self.invariant()
        self.assertNotEqual(key, oldkey)
        self.assertNotEqual(domains.library_product_key(old, 'c'*64),
                            domains.library_product_key(request, 'c'*64))
        self.assertEqual(builder.load_request(self.requests, key, self.cohort)[0], request)

    def test_false_numeric_string_and_absent_invariance_are_rejected(self):
        for value in (False, 0, 1, 'true', None):
            with self.subTest(value=value):
                key, request = self.invariant(preserve_invariance=value)
                with self.assertRaises(builder.Refused):
                    builder.load_request(self.requests, key, self.cohort)
                with self.assertRaises(ValueError):
                    domains.library_product_key(request, 'c'*64)
        key, request = self.invariant()
        request.pop('preserve_invariance')
        (self.requests / (key+'.json')).write_bytes(builder.canonical(request))
        with self.assertRaises(builder.Refused):
            builder.load_request(self.requests, key, self.cohort)

    def test_cannot_smuggle_option_into_legacy_schema_or_wrong_recipe(self):
        for changes in (dict(schema=1, recipe=builder.RECIPE),
                        dict(recipe=builder.RECIPE), dict(schema=2)):
            key, request = self.invariant(**changes)
            with self.assertRaises(builder.Refused):
                builder.load_request(self.requests, key, self.cohort)

    def test_corrupted_key_source_or_extra_field_rejected(self):
        key, request = self.invariant()
        for mutation in (dict(key='f'*64), dict(source_sha256='f'*64), dict(extra='ignored?')):
            (self.requests / (key+'.json')).write_bytes(builder.canonical(dict(request, **mutation)))
            with self.assertRaises(builder.Refused):
                builder.load_request(self.requests, key, self.cohort)

    def test_builder_compile_command_and_warm_receipt_binding(self):
        self.runner = InvariantRunner(self.case)
        key, request = self.invariant()
        first = self.run_builder()
        self.assertEqual(first['status'], 'passed', first)
        self.assertEqual(first['built'], 1)
        self.assertIn('-fpreserve-invariance', self.runner.compiles()[0])
        self.assertTrue(all(argv[:3]==['/usr/bin/xcrun','--sdk','macosx']
                            for argv in self.runner.calls if argv[0]=='/usr/bin/xcrun'))
        receipt = json.loads(self.artifact(first, key, '.json').read_bytes())
        self.assertIs(receipt['preserve_invariance'], True)
        before = self.artifact(first, key, '.metallib').read_bytes()
        second = self.run_builder()
        self.assertEqual(second['status'], 'passed', second)
        self.assertEqual(second['built'], 0)
        self.assertEqual(len(self.runner.compiles()), 1)
        self.assertEqual(self.artifact(first, key, '.metallib').read_bytes(), before)
        receipt['preserve_invariance'] = False
        self.artifact(first, key, '.json').write_bytes(builder.canonical(receipt))
        with self.assertRaises(builder.Refused):
            builder.already_succeeded(self.folder/'artifacts'/first['build_id'],
                                      request, first['build_id'])

    def test_compile_option_product_preserved_across_cohorts(self):
        self.runner = InvariantRunner(self.case)
        key, _ = self.invariant()
        first = self.run_builder()
        self.assertEqual(first['status'], 'passed', first)
        previous = self.artifact(first, key, '.metallib')
        before = previous.read_bytes()
        self.select_cohort('b'*64)
        self.invariant()
        second = self.run_builder()
        self.assertEqual(second['status'], 'passed', second)
        self.assertEqual(second['reused_products'], 1)
        self.assertEqual(second['built'], 0)
        self.assertEqual(len(self.runner.compiles()), 1)
        self.assertEqual(previous.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
