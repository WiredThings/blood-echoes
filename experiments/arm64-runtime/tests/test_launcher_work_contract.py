"""Source wiring controls only. C++ filesystem cases await unified native build."""
from pathlib import Path
import unittest

D = Path(__file__).resolve().parents[1]


class Controls(unittest.TestCase):
    def setUp(self):
        self.game = (D / 'src/game_runtime.cpp').read_text()
        self.session = (D / 'src/session_state.h').read_text()
        self.helper = (D / 'src/prepare_guest_module_info.h').read_text()

    def test_existing_qa_source_validation_precedes_work_creation(self):
        source = self.game.index('BbSession::Prepare(state,fs::path(BB_PROTOTYPE_ROOT)/"state",identity,resume);')
        work = self.game.index('runtime_work=bundled?BbSession::PrepareBundledLauncherWork(')
        self.assertLess(source, work)
        self.assertIn('if(!bundled)BbSession::Prepare', self.game)
        fragment = self.game[source:work]
        self.assertIn('fs::path runtime_work=state;', fragment)
        self.assertIn('if(launcher_compat)', fragment)
        self.assertIn('check(getenv("BB_DATA_DIR")', fragment)

    def test_progress_user_and_normal_save_wiring_are_not_redirected(self):
        self.assertIn('state=options.user;', self.game)
        self.assertIn('runtime_file_configure(app0.c_str(),state.c_str());runtime_savedata_configure(game_metadata.saves.c_str());', self.game)
        self.assertNotIn('runtime_file_configure(app0.c_str(),runtime_work', self.game)
        self.assertIn('fs::copy(source,state/"savedata",', self.game)

    def test_all_identified_runtime_reports_use_work(self):
        for name in ('imports.tsv', 'cpu-code-manifest.json', 'cpu-cache-environment.json'):
            self.assertIn('runtime_work/"' + name + '"', self.game)
            self.assertNotIn('state/"' + name + '"', self.game)
        self.assertIn('CodeMapOutput>(runtime_work/("cpu-code-"', self.game)
        self.assertIn('BbGuestModules::Prepare(boot,app0,runtime_work)', self.game)
        self.assertIn('ARM LAUNCHER runtime work=%s; progress=%s;', self.game)

    def test_actual_disk_cache_and_legacy_state_paths_remain(self):
        self.assertIn('fs::canonical(cache_path,cache_error)', self.game)
        self.assertIn('if(cache_path&&*cache_path)', self.game)
        self.assertNotIn('cache_path=runtime_work', self.game)
        self.assertIn('fs::path runtime_work=state;', self.game)
        self.assertIn('auto marker=state/".arm-runtime-session";', self.session)

    def test_work_uses_exact_original_data_and_private_root(self):
        self.assertIn('fs::path(getenv("BB_DATA_DIR")),fs::path(BB_PROTOTYPE_ROOT)/"state",launcher_config,boot,content,session_nonce,identity', self.game)
        for check in ('config==data/"bbport.ini"', 'boot==data/"out/boot-linked.bin"', 'content==data/"out/content.bin"',
                      'relative!="."', '*relative.begin()!=".."', 'fs::canonical(data)==data'):
            self.assertIn(check, self.session)
        self.assertNotIn('getenv(', self.session)

    def test_work_owned_permissions_nofollow_and_complete_identity(self):
        for marker in ('mkdirat(parent,name,0700)', 'O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC', 'st_uid==getuid()',
                       'st_nlink==1', '(before.st_mode&0777)==0600', '(value.st_mode&0777)==0700',
                       'SameWorkNode(before,named)', 'actual==identity', 'identity.ends_with("\\n"+nonce+"\\n")',
                       'O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600', 'fsync(marker.get())'):
            self.assertIn(marker, self.session)

    def test_helper_code_root_selector_is_preserved(self):
        self.assertIn('const auto output=runtime_work/".arm-module-info.bin";', self.helper)
        self.assertIn('const auto selected=ActualHelper();', self.helper)
        self.assertIn('_NSGetExecutablePath(path,&size)', self.helper)
        self.assertIn('app.parent_path()!=project/"apps"', self.helper)
        self.assertIn('"--runtime-executable",selected.executable.string()', self.helper)
        self.assertNotIn('getenv("BB_CODE_ROOT', self.helper)

    def test_restart_user_gate_and_exit_protocol_untouched(self):
        restart = (D / 'src/native_restart.c').read_text()
        self.assertIn('runtime_file_user_dir()', restart)
        self.assertIn('BB_PROTOTYPE_ROOT', restart)
        self.assertIn('strchr(state+strlen(root)', restart)
        self.assertIn('runtime_supervised_restart_status()', restart)
        self.assertIn('bbgpu_before_restart()', restart)
        self.assertIn('_Exit(status)', restart)

    def test_existing_native_target_has_real_pending_filesystem_controls(self):
        test = (D / 'tests/test_session_state.cpp').read_text()
        for marker in ('PrepareLauncherWork', 'source_time', 'base-game', 'content.string()',
                       'create_hard_link', 'runtime-sessions-owned', 'group_read', 'existing work is never silently adopted'):
            self.assertIn(marker, test)
        self.assertIn('add_executable(test_session_state', (D / 'targets.cmake').read_text())


if __name__ == '__main__':
    unittest.main(verbosity=2)
