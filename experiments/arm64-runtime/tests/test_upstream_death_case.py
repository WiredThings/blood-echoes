#!/usr/bin/env python3
"""Pure routing/protocol controls; do not start bootstrap, compiler or game."""
import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('upstream_death_case',HERE/'run_upstream_death_case.py')
case=importlib.util.module_from_spec(spec);sys.modules[spec.name]=case;spec.loader.exec_module(case)


class DeathCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stage=case.APP/'Contents/Resources/bbport'
        cls.inputs={name:(cls.stage/name).read_bytes() for name in case.EXPECTED}
        cls.output=case.PROJECT/'evidence/upstream-private-death-run-998877665544332211'

    def test_exact_route_projection_and_original_sources_unchanged(self):
        projected=case.projected_routes(self.inputs,self.stage,self.output)
        self.assertEqual(set(projected),{'run.sh','supervise.sh','run_optimized.sh'})
        self.assertIn(b'local_metal_startup=',projected['run.sh'])
        self.assertIn(str(self.output/'handoff.py').encode(),projected['run.sh'])
        # Keep the background launch, wait and trap that own live merged app0.
        for anchor in (b'"$probe" "${probe_args[@]}" &',b'wait "$mod_pid"',b'trap ',b'--content-profile'):
            self.assertIn(anchor,projected['run.sh'])
        for anchor in (b'75)',b'76)',b'export BB_ONLINE=0 BB_SKIP_NETWORK_CHOICE=1'):
            self.assertIn(anchor,projected['supervise.sh'])
        for name,raw in self.inputs.items():self.assertEqual(raw,(self.stage/name).read_bytes())

    def test_any_original_byte_mutation_or_formal_path_is_rejected(self):
        for name in self.inputs:
            changed=dict(self.inputs);changed[name]+=b'\n'
            with self.subTest(name=name),self.assertRaises(ValueError):
                case.projected_routes(changed,self.stage,self.output)
        with self.assertRaises(ValueError):
            case.projected_routes(self.inputs,Path('/Applications/Blood Echoes.app/Contents/Resources/bbport'),self.output)

    def fixture(self):
        data=case.PROJECT/'state/benchmark-data-123'
        user=case.PROJECT/'state/benchmark-user-123'
        normal=str(self.stage/case.CPU_ROLE)
        env=dict(BB_ONLINE='0',BB_AUDIO='none',BB_QUICKSAVE='0',BB_MTL_RELAX_VERTEX='0',
                 BB_DMEM='123456',BB_ARBITRARY_REAL_RUNTIME_VALUE='unchanged')
        manifest=dict(original_cpu=normal,private_cpu=str(case.CPU_ROOT/'code/bb-game-arm64'),
            required_environment={k:env[k] for k in ('BB_ONLINE','BB_AUDIO','BB_QUICKSAVE')},user=str(user),
            boot=str(data/'out/boot-linked.bin'),patches=str(data/'out/patches.bin'),content=str(data/'out/content.bin'))
        argv=[normal,manifest['boot'],'--content-profile',manifest['content'],'--patches',manifest['patches'],
              '--app0',str(data/'live-community-merged-view'),'--user',str(user),'--timeout','0']
        return manifest,argv,env

    def test_dispatch_preserves_every_real_argument_and_environment_value(self):
        manifest,argv,env=self.fixture()
        original=copy.deepcopy((argv,env))
        target,args,final=case.dispatch_arguments(argv[0],argv,env,manifest)
        self.assertEqual(target,manifest['private_cpu'])
        self.assertEqual(args[1:],argv[1:])
        self.assertEqual(final,env)
        self.assertEqual((argv,env),original)
        self.assertIn('live-community-merged-view',args[args.index('--app0')+1])

    def test_source_cpu_cache_absent_empty_and_enabled_semantics(self):
        private=case.CPU_ROOT/'private-namespace/startup.bin'
        for source,mode,present,expected in (({},'absent',False,None),({'BB_ARM_CPU_CACHE':''},'empty',True,''),
                ({'BB_ARM_CPU_CACHE':'/original/normal-cache.bin'},'enabled',True,str(private))):
            with self.subTest(mode=mode):
                control,delta=case.cpu_cache_control(source,private)
                self.assertEqual(control['source_mode'],mode)
                self.assertEqual(delta,{'BB_ARM_CPU_CACHE':str(private)} if mode=='enabled' else {})
                actual=dict(source);actual.update(delta)
                seen=case.cpu_cache_observation(control,actual)
                self.assertEqual(seen['actual_present'],present);self.assertEqual(seen['actual_value'],expected)
                manifest,args,env=self.fixture();env.update(actual)
                manifest['cpu_cache_control']=control;manifest['required_environment']['BB_ARM_CPU_CACHE']=expected
                self.assertEqual(case.dispatch_arguments(args[0],args,env,manifest)[2],env)

    def test_cpu_cache_cannot_be_added_cleared_or_switched_by_bootstrap(self):
        for source in ({},{'BB_ARM_CPU_CACHE':''},{'BB_ARM_CPU_CACHE':'/original/cache.bin'}):
            control,delta=case.cpu_cache_control(source,case.CPU_ROOT/'private/startup.bin')
            manifest,args,env=self.fixture();env.update(source);env.update(delta)
            manifest['cpu_cache_control']=control;manifest['required_environment']['BB_ARM_CPU_CACHE']=control['expected_value']
            mutants=[dict(env,BB_ARM_CPU_CACHE='0'),dict(env,BB_ARM_CPU_CACHE='/unexpected/cache.bin')]
            missing=dict(env);missing.pop('BB_ARM_CPU_CACHE',None)
            if control['expected_present']:mutants.append(missing)
            if not control['expected_present']:mutants.append(dict(env,BB_ARM_CPU_CACHE=''))
            for changed in mutants:
                with self.subTest(source=source,changed=changed.get('BB_ARM_CPU_CACHE')),self.assertRaises(ValueError):
                    case.dispatch_arguments(args[0],args,changed,manifest)
        for value in ('0',0,None,False):
            with self.subTest(value=value),self.assertRaises(ValueError):
                case.cpu_cache_control({'BB_ARM_CPU_CACHE':value},case.CPU_ROOT/'private/startup.bin')

    def test_vertex_on_is_one_exact_authorized_delta_after_original_validation(self):
        for source in ({},{case.VERTEX_FLAG:'0'}):
            base=dict(source,BB_AUDIO='none',BB_DMEM='original')
            final=dict(base,**{case.VERTEX_FLAG:'1'})
            record=case.vertex_change(source,base,final,enabled=True)
            self.assertEqual(record['actual_changes'],{case.VERTEX_FLAG:dict(before=source.get(case.VERTEX_FLAG),after='1')})
            self.assertEqual(record['allowed_changes'],[case.VERTEX_FLAG])
            self.assertTrue(record['ordinary_environment_checked_first'])
            self.assertEqual(case.vertex_change(source,base,base,enabled=False)['actual_changes'],{})
            manifest,args,_=self.fixture();manifest['required_environment'][case.VERTEX_FLAG]='1'
            env=dict(self.fixture()[2],**{case.VERTEX_FLAG:'1'})
            self.assertEqual(case.dispatch_arguments(args[0],args,env,manifest)[2][case.VERTEX_FLAG],'1')
            env[case.VERTEX_FLAG]='0'
            with self.assertRaises(ValueError):case.dispatch_arguments(args[0],args,env,manifest)

    def test_vertex_bad_source_prechange_extra_mutation_or_resume_rejected(self):
        for value in ('1','2','',None,0,False):
            with self.subTest(value=value),self.assertRaises(ValueError):
                case.vertex_change({case.VERTEX_FLAG:value},{case.VERTEX_FLAG:value},{case.VERTEX_FLAG:'1'},enabled=True)
        with self.assertRaises(ValueError):case.vertex_change({}, {case.VERTEX_FLAG:'0'}, {case.VERTEX_FLAG:'1'},enabled=True)
        with self.assertRaises(ValueError):case.vertex_change({}, {}, {case.VERTEX_FLAG:'1','BB_UNRELATED':'1'},enabled=True)
        with self.assertRaises(ValueError):case.vertex_change({}, {}, {case.VERTEX_FLAG:'1'},enabled=False)
        with self.assertRaisesRegex(ValueError,'fresh isolated'):
            case.execute(SimpleNamespace(reviewed_code=True,seconds=1200,vertex_on=True,resume_from=Path('/old')))

    def test_vertex_control_preserves_all_source_cpu_cache_modes(self):
        for source in ({},{'BB_ARM_CPU_CACHE':''},{'BB_ARM_CPU_CACHE':'/normal/cache.bin'}):
            control,delta=case.cpu_cache_control(source,case.CPU_ROOT/'new/startup.bin')
            base=dict(source);final=dict(base,**{case.VERTEX_FLAG:'1'})
            case.vertex_change(source,base,final,enabled=True)
            final.update(delta)
            self.assertTrue(case.cpu_cache_observation(control,final)['source_semantics_preserved'])

    def test_wrong_exec_repeated_options_changed_input_or_online_are_rejected(self):
        for mutation in ('target','argv0','user','boot','patches','content','app0','duplicate','online','token'):
            manifest,argv,env=self.fixture();target=argv[0]
            if mutation=='target':target='/usr/bin/true'
            elif mutation=='argv0':argv[0]='/usr/bin/true'
            elif mutation=='boot':argv[1]='/tmp/other.bin'
            elif mutation in ('user','patches','content','app0'):
                flag={'content':'--content-profile'}.get(mutation,'--'+mutation)
                argv[argv.index(flag)+1]='/Users/Shared/BloodEchoesDevelopment/../../tmp/other'
            elif mutation=='duplicate':argv+=['--app0',argv[argv.index('--app0')+1]]
            elif mutation=='online':env['BB_ONLINE']='1'
            else:env['BB_THD_TOKEN']='not-a-real-token'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                case.dispatch_arguments(target,argv,env,manifest)

    def test_module_local_proxy_never_changes_global_os_and_execs_only_once(self):
        called=[];environ={'key':'same-object'}
        def original_exec(*args):called.append(args)
        original=SimpleNamespace(execve=original_exec,environ=environ)
        proxy=case.ExecOnlyProxy(original,lambda target,args,env:('QA',[*args],dict(env)))
        self.assertIs(proxy.environ,environ)
        proxy.execve('normal',['normal','boot'],{'key':'real'})
        self.assertIs(original.execve,original_exec)
        self.assertEqual(called,[('QA',['normal','boot'],{'key':'real'})])
        with self.assertRaises(ValueError):proxy.execve('normal',[],{})
        self.assertEqual(len(called),1)

    def status(self,t,seq=0,**kwargs):
        return dict(schema=1,time=t,error='',world=True,loading=False,health_valid=True,
                    hp=100,max_hp=100,stable=5,player=42,death_sequence=seq,**kwargs)

    def row(self,t,seq=0,**kwargs):
        value=self.status(t,seq);value.update(kwargs);return value

    def test_two_real_unload_load_cycles_even_with_same_player_address(self):
        check=case.DeathCheck()
        self.assertEqual(check.step(1,self.row(1)),1)
        self.assertIsNone(check.step(2,self.row(2,1,hp=0)))
        self.assertIsNone(check.step(3,self.row(3,1,world=False,loading=True,hp=0,player=0)))
        self.assertIsNone(check.step(4,self.row(4,1)))
        self.assertIsNone(check.step(8.9,self.row(8.9,1)))
        self.assertEqual(check.step(9,self.row(9,1)),2)
        check.step(10,self.row(10,2,hp=0))
        check.step(11,self.row(11,2,world=False,loading=True,hp=0,player=0))
        check.step(12,self.row(12,2),frame_clock=100)
        self.assertIsNone(check.step(21.9,self.row(21.9,2),frame_clock=109))
        self.assertIsNone(check.step(22,self.row(22,2),frame_clock=110))
        self.assertEqual(check.phase,'completed');self.assertEqual(len(check.cycles),2)
        self.assertEqual([row['player'] for row in check.cycles],[42,42])
        self.assertEqual([row['stable_seconds'] for row in check.cycles],[5,10])

    def confirming_second(self):
        check=case.DeathCheck();check.step(1,self.row(1));check.step(2,self.row(2,1,hp=0))
        check.step(3,self.row(3,1,world=False,loading=True));check.step(4,self.row(4,1));check.step(9,self.row(9,1))
        check.step(10,self.row(10,2,hp=0));check.step(11,self.row(11,2,world=False,loading=True))
        check.step(12,self.row(12,2),frame_clock=100)
        return check

    def test_second_confirmation_requires_frame_advance_after_ten_second_boundary(self):
        check=self.confirming_second()
        check.step(13,self.row(13,2),frame_clock=101)
        self.assertIsNone(check.step(22,self.row(22,2),frame_clock=101))
        self.assertEqual(check.phase,'confirming-second')
        check.step(22.2,self.row(22.2,2),frame_clock=102)
        self.assertEqual(check.phase,'completed')

    def test_stability_rejects_new_loading_death_health_failure_or_lost_cpu(self):
        for field,value in (('world',False),('loading',True),('hp',0),('health_valid',False)):
            check=self.confirming_second()
            with self.subTest(field=field),self.assertRaises(ValueError):
                check.step(14,self.row(14,2,**{field:value}),frame_clock=102)
        check=self.confirming_second()
        with self.assertRaises(ValueError):check.step(14,self.row(14,2),cpu_alive=False,frame_clock=102)
        check=self.confirming_second()
        with self.assertRaises(ValueError):check.step(14,self.row(14,2),frame_clock=99)
        # Confirmation cannot reset or extend the original cycle budget.
        check=self.confirming_second()
        with self.assertRaises(ValueError):check.step(190,self.row(190,2),frame_clock=100)

    def test_missing_each_required_signal_cannot_complete(self):
        for missing in ('hp_zero','world_down','loading'):
            check=case.DeathCheck();check.step(1,self.row(1))
            check.step(2,self.row(2,1,hp=100 if missing=='hp_zero' else 0))
            check.step(3,self.row(3,1,hp=100 if missing=='hp_zero' else 0,
                world=missing=='world_down',loading=missing!='loading'))
            self.assertIsNone(check.step(4,self.row(4,1)))
            self.assertEqual(check.sequence,1)
            with self.assertRaises(ValueError):check.step(182,self.row(182,1))

    def test_command_ack_lag_does_not_count_old_evidence(self):
        check=case.DeathCheck();check.step(1,self.row(1))
        self.assertIsNone(check.step(1.2,self.row(1.2,0,hp=0,world=False,loading=True)))
        self.assertFalse(any(check.events.values()))
        with self.assertRaises(ValueError):check.step(6.1,self.row(6.1,0))

    def test_stale_clock_unexpected_sequence_and_bad_status_rejected(self):
        for bad in ('clock','sequence','error','flags','host-clock'):
            check=case.DeathCheck();check.step(1,self.row(1));row=self.row(2,1)
            seconds=2
            if bad=='clock':row['time']=0
            elif bad=='sequence':row['death_sequence']=3
            elif bad=='error':row['error']='rejected-death-command'
            elif bad=='flags':row['world']='true'
            else:seconds=0
            with self.subTest(bad=bad),self.assertRaises(ValueError):check.step(seconds,row)

    def test_original_policy_precedes_every_qa_action(self):
        original=SimpleNamespace(observe=Mock(return_value='screen locked'))
        adapter=case.DeathPolicy(original,None,{},'/unused',Path('/unused'),1200)
        with patch.object(case,'atomic') as write:
            self.assertEqual(adapter.observe(1,{}),'screen locked');write.assert_not_called()
        original.observe.assert_called_once_with(1,{})

    def test_replaced_or_overlapping_cpu_cannot_continue_death_sequence(self):
        original=SimpleNamespace(observe=Mock(return_value=None))
        for pids in ([2],[1,2]):
            adapter=case.DeathPolicy(original,None,{},'/QA-cpu',Path('/unused'),1200)
            adapter.cpu_pid=1
            rows=[(pid,9,'/QA-cpu') for pid in pids]
            with self.subTest(pids=pids),self.assertRaises(ValueError):
                adapter.observe(1,dict(processes=rows))

    def test_disappeared_cpu_rejected_before_reading_old_status(self):
        original=SimpleNamespace(observe=Mock(return_value=None))
        adapter=case.DeathPolicy(original,None,{},'/QA-cpu',Path('/unused'),1200)
        adapter.cpu_pid=1;adapter.armed=True;adapter.check.last_time=123
        with patch.object(case,'plain_json',return_value=self.row(123,1)) as read:
            with self.assertRaisesRegex(ValueError,'CPU disappeared'):
                adapter.observe(5,dict(processes=[]))
            read.assert_not_called()

    def test_real_zero_byte_and_partial_frame_headers_do_not_arm_or_bypass_budget(self):
        sys.path.insert(0,str(HERE.parent/'tools'))
        from diagnostic_frame_tail import read_tail
        for raw in (b'',b't_s,dt_ms,draw'):
            with self.subTest(raw=raw),tempfile.TemporaryDirectory() as folder:
                data=Path(folder);(data/'out').mkdir();(data/'out/frames.csv').write_bytes(raw)
                original=SimpleNamespace(observe=Mock(return_value=None))
                driver=SimpleNamespace(read_tail=read_tail,scene_seen=Mock(side_effect=AssertionError('no complete rows')))
                adapter=case.DeathPolicy(original,driver,dict(data_root=data),'/QA-cpu',data/'world',1200)
                with patch.object(case,'atomic') as write:
                    self.assertIsNone(adapter.observe(1,dict(processes=[(2,1,'/QA-cpu')])))
                    self.assertFalse(adapter.armed);write.assert_not_called()
                    self.assertEqual(adapter.observe(1200,{}),'private death total time budget')

    def test_final_functional_pass_needs_exact_stop_two_cycles_seals_and_handoff(self):
        manifest,args,env=self.fixture();bridge='a'*64
        receipt=dict(original_argv=args,actual_argv=[manifest['private_cpu'],*args[1:]],
            original_cpu=manifest['original_cpu'],private_cpu=manifest['private_cpu'],private_bridge_build=bridge,
            argv_tail_unchanged=True,original_preparation_ran=True,normal_role_impersonated=False,performance_sample=False,
            final_mod_app0=args[args.index('--app0')+1])
        check=case.DeathCheck(phase='completed',cycles=[dict(sequence=i,evidence=dict(hp_zero=True,world_down=True,loading=True),
            stable_seconds=5 if i==1 else 10,cpu_alive_confirmed=True,frame_progress_confirmed=i==2) for i in (1,2)])
        row=dict(status='incomplete',stop=case.COMPLETED,source_save_unchanged=True,
            whole_app_seal=dict(pre=dict(status='verified'),post=dict(status='verified'),post_after_owned_process_cleanup=True))
        result=case.finalize_result(row,check,receipt,manifest,bridge)
        self.assertEqual(result['status'],'functional-checked');self.assertEqual(result['raw_driver_status'],'incomplete')
        for mutation in ('protection','cycles','source','seal','handoff','old-argv','short-stability','frozen-frame'):
            changed,control,proof=copy.deepcopy(row),copy.deepcopy(check),copy.deepcopy(receipt)
            if mutation=='protection':changed['stop']='unexpected >2GiB swap growth'
            elif mutation=='cycles':control.cycles.pop()
            elif mutation=='source':changed['source_save_unchanged']=False
            elif mutation=='seal':changed['whole_app_seal']['post']['status']='failed'
            elif mutation=='handoff':proof=None
            elif mutation=='short-stability':control.cycles[1]['stable_seconds']=9.99
            elif mutation=='frozen-frame':control.cycles[1]['frame_progress_confirmed']=False
            else:proof['actual_argv'][1]='/wrong.bin'
            with self.subTest(mutation=mutation):
                failed=case.finalize_result(changed,control,proof,manifest,bridge)
                self.assertEqual(failed['status'],'incomplete');self.assertFalse(failed['functional_death']['passed'])

    def test_old_cpu_without_separate_cache_identity_rejected(self):
        root=case.PROJECT/'evidence/upstream-private-death-20261030'
        with self.assertRaisesRegex(ValueError,'accurate separate bridge identity'):
            case.verify_cpu(root)

    def test_execution_requires_review_before_any_admission(self):
        with self.assertRaisesRegex(ValueError,'independent review'):
            case.execute(SimpleNamespace(reviewed_code=False))


if __name__=='__main__':unittest.main()
