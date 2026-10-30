"""Exact opt-in F3 compiler-control tests, without native admission or games."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_upstream_refresh_game as harness
import test_upstream_game_harness as controls


class ExplicitVertexOnF3(unittest.TestCase):
    def setUp(self):
        p=patch('subprocess.Popen',side_effect=AssertionError('no subprocess/game permitted'))
        p.start();self.addCleanup(p.stop)

    def fixture(self,*args,**kwargs):
        return controls.UpstreamGameHarness.environment_fixture(self,*args,**kwargs)

    def args(self,*flags):
        return harness.argument_parser().parse_args(['--plan','--app',str(harness.PROJECT/'evidence/candidate.app'),
                                                    '--vertex-on-f3',*flags])

    def test_explicit_on_changes_only_one_field_after_full_ordinary_check(self):
        for source_flag in ('absent','0'):
            with self.subTest(source_flag=source_flag):
                driver,source,case,calls=self.fixture(source_flag)
                original=copy.deepcopy(source)
                final,changes,allowed,evidence=harness.controlled_environment(driver,source,case,{},'B',
                    functional_f3=True,vertex_on_f3=True)
                self.assertEqual(len(calls),1)
                self.assertEqual(calls[0].get(harness.VERTEX_FLAG),source['environment'].get(harness.VERTEX_FLAG))
                self.assertEqual({k for k in set(final)|set(calls[0]) if final.get(k)!=calls[0].get(k)}, {harness.VERTEX_FLAG})
                self.assertEqual(final[harness.VERTEX_FLAG],'1')
                self.assertEqual(final['BB_QUICKSAVE'],'1');self.assertEqual(final['BB_FRAME_STATS'],'1')
                self.assertEqual(final['BB_AUDIO'],'none')
                self.assertEqual(evidence['mode'],'vertex-on-functional-f3')
                self.assertFalse(evidence['functional_f3_default_off'])
                self.assertTrue(evidence['functional_f3_explicit_on'])
                self.assertFalse(evidence['comparison_sample'])
                self.assertEqual(evidence['actual_experiment_changes'],{harness.VERTEX_FLAG:changes[harness.VERTEX_FLAG]})
                self.assertEqual(evidence['allowed_experiment_variables'],[harness.VERTEX_FLAG])
                self.assertIn(harness.VERTEX_FLAG,allowed)
                self.assertEqual(source,original)

    def test_other_setting_rejected_by_ordinary_admission_before_opt_in(self):
        for extra in ({'BB_UNREVIEWED':'1'},{'BB_AUDIO':'enabled'},{harness.VERTEX_FLAG:'1'}):
            with self.subTest(extra=extra):
                driver,source,case,calls=self.fixture(extra_base=extra)
                with self.assertRaisesRegex(ValueError,'unapproved game setting'):
                    harness.controlled_environment(driver,source,case,{},'B',functional_f3=True,vertex_on_f3=True)
                self.assertEqual(len(calls),1)

    def test_other_final_field_or_missing_vertex_on_is_not_allowed(self):
        base={'BB_AUDIO':'none','BB_QUICKSAVE':'1'}
        for final in (dict(base),dict(base,BB_MTL_RELAX_VERTEX='0'),
                      dict(base,BB_MTL_RELAX_VERTEX='1',BB_AUDIO='enabled')):
            with self.assertRaisesRegex(ValueError,'unreviewed environment'):
                harness.vertex_on_f3_delta({},base,final,functional_f3=True)

    def test_opt_in_cannot_apply_to_abba_or_nonfunctional_or_variant_a(self):
        for kwargs in (dict(vertex_ab=True,functional_f3=True),dict(functional_f3=False)):
            driver,source,case,calls=self.fixture()
            with self.assertRaisesRegex(ValueError,'single candidate functional F3'):
                harness.controlled_environment(driver,source,case,{},'B',vertex_on_f3=True,**kwargs)
            self.assertEqual(calls,[])
        driver,source,case,calls=self.fixture()
        with self.assertRaisesRegex(ValueError,'single candidate functional F3'):
            harness.controlled_environment(driver,source,case,{},'A',vertex_on_f3=True,functional_f3=True)
        self.assertEqual(calls,[])

    def test_cli_only_permits_separate_f3_without_other_modes_or_resume(self):
        for flags in ((),('--phase','performance'),('--phase','all'),('--phase','f3','--vertex-ab'),
                      ('--phase','f3','--resume-from','/tmp/previous')):
            with self.subTest(flags=flags):
                with self.assertRaisesRegex(ValueError,'requires only --phase f3'):
                    harness.selected_applications(self.args(*flags))
        args=self.args('--phase','f3')
        self.assertTrue(harness.vertex_on_f3_request(args))
        self.assertEqual(harness.selected_applications(args),(args.app,args.app))
        self.assertEqual(harness.schedule(args.phase,vertex_ab=args.vertex_ab),[('functional-F2-F3','B',True)])

    def test_explicit_on_cannot_select_a_distinct_baseline_app(self):
        args=self.args('--phase','f3','--baseline-app',str(harness.PROJECT/'evidence/other.app'))
        with self.assertRaisesRegex(ValueError,'single candidate app'):harness.selected_applications(args)

    def test_plan_records_explicit_on_without_abba_or_receipt_mutation(self):
        args=self.args('--phase','f3')
        receipt=dict(producer='original-swift-LauncherModel.start-final-environment',
                     environment=dict(BB_AUDIO='none',BB_ONLINE='0'))
        original=copy.deepcopy(receipt)
        with patch.object(harness,'read_object',side_effect=lambda path:receipt if path==args.receipt else None):
            plan=harness.plan(args)
        self.assertEqual(receipt,original)
        self.assertEqual(plan['comparison_mode'],'vertex-on-functional-f3')
        self.assertEqual(plan['vertex_control']['F3'],'1')
        self.assertTrue(plan['vertex_control']['functional_f3_explicit_on'])
        self.assertFalse(plan['vertex_control']['comparison_sample'])
        self.assertIn('--vertex-on-f3',plan['thin_controller_command_after_review'])
        self.assertNotIn('--vertex-ab',plan['thin_controller_command_after_review'])
        self.assertEqual(plan['warm_series']['sequence'],['single candidate explicit vertex-on F2/F3 session'])
        self.assertFalse(plan['source_receipt_edited']);self.assertFalse(plan['admission_performed'])

    def test_previous_abba_f3_still_explicitly_default_off(self):
        driver,source,case,calls=self.fixture()
        final,changes,allowed,evidence=harness.controlled_environment(driver,source,case,{},'B',
            vertex_ab=True,functional_f3=True)
        self.assertNotIn(harness.VERTEX_FLAG,final)
        self.assertTrue(evidence['functional_f3_default_off'])
        self.assertNotIn('functional_f3_explicit_on',evidence)
        self.assertEqual(evidence['actual_experiment_changes'],{})


if __name__=='__main__':unittest.main(verbosity=2)
