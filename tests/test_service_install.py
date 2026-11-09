import json
from pathlib import Path, PurePosixPath
import tempfile
import unittest
from unittest.mock import patch, Mock
from test_tools import tool

FIXTURE = Path(__file__).parent / "fixtures/S99rkllama"


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.module = tool("install_backend")
        self.original = FIXTURE.read_text(encoding="utf-8")
    def test_patch_delegates_only_model_service_preserving_web_block(self):
        patched = self.module.patch_startup(self.original, PurePosixPath("/userdata/local-ai-candidate-v010"))
        start = '    if is_running "$WEB_PID"; then'
        web = self.original[self.original.index(start):self.original.index('\nstop_one()')]
        self.assertIn(web.replace('echo "RKLLama API:', 'echo "Independent AI API:'), patched)
        self.assertNotIn('"$VENV/bin/rkllama_server" \\', patched)
        self.assertIn('service_control.py" start || return 1', patched)
        self.assertIn('service_control.py" status --quiet', patched)
        self.assertIn('service_control.py" stop', patched)
        self.assertIn('if [ "$2" = "$RK_PID" ]; then', patched)
        self.assertIn('"$WEB_PID") expected="$WEB_APP"', patched)
    def test_unknown_layout_and_repeated_install_are_rejected(self):
        with self.assertRaises(self.module.TrialError): self.module.patch_startup('unknown', PurePosixPath('/userdata/app'))
        patched = self.module.patch_startup(self.original, PurePosixPath('/userdata/app'))
        with self.assertRaises(self.module.TrialError): self.module.patch_startup(patched, PurePosixPath('/userdata/app'))
    def test_path_with_shell_substitution_is_rejected(self):
        with self.assertRaises(self.module.TrialError): self.module.patch_startup(self.original, PurePosixPath('/userdata/$(bad)'))
    def test_duplicate_model_start_block_is_rejected(self):
        source = self.original + self.original[self.original.index('    if is_running "$RK_PID"; then'):self.original.index('\n    if is_running "$WEB_PID"; then')]
        with self.assertRaises(self.module.TrialError): self.module.patch_startup(source, PurePosixPath('/userdata/app'))
    def test_atomic_file_restore_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'startup'
            path.write_bytes(b'old')
            self.module.atomic_write(path, b'new\n', 0o755)
            self.assertEqual(path.read_bytes(), b'new\n')
            self.assertEqual(list(Path(folder).iterdir()), [path])
    def test_rollback_waits_for_old_api_instead_of_checking_immediately(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = root / 'backups/test'; backup.mkdir(parents=True)
            original = self.original.encode()
            patched = self.module.patch_startup(self.original, PurePosixPath('/userdata/app')).encode()
            startup = root / 'S99rkllama'; startup.write_bytes(patched)
            (backup / 'S99rkllama.original').write_bytes(original)
            metadata = {'root':str(root), 'original_sha256':self.module.sha(original),
                        'patched_sha256':self.module.sha(patched), 'mode':0o755}
            (backup / 'backup.json').write_text(json.dumps(metadata))
            with patch.object(self.module, 'ROOT', root), patch.object(self.module, 'STARTUP', startup), \
                 patch.object(self.module, 'json_get', return_value={'running':False}), \
                 patch.object(self.module, 'service') as service, \
                 patch.object(self.module.subprocess, 'run'), \
                 patch.object(self.module, 'tags_ready', side_effect=[OSError('starting'), True]) as ready:
                self.module.rollback(backup)
            self.assertEqual(ready.call_count, 2)
            self.assertEqual(startup.read_bytes(), original)
            service.assert_called_once_with('stop')


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.module = tool('service_control')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.state = self.folder / 'state.json'
        self.pidfile = self.folder / 'pid'
        self.valid = {'pid':123, 'start':'A', 'argv':['python', '-m', 'local_ai.server',
            '--config', str(self.module.ROOT / 'config.local.json'), '--port', '8080', '--enable-inference']}
    def test_expected_requires_module_config_port_and_explicit_inference(self):
        self.assertTrue(self.module.expected(self.valid))
        for key, value in [('local_ai.server','unrelated'), ('8080','18080'),
                           (str(self.module.ROOT / 'config.local.json'), 'other.json'), ('--enable-inference','')]:
            info = dict(self.valid, argv=[value if a == key else a for a in self.valid['argv']])
            self.assertFalse(self.module.expected(info))
    def test_reused_pid_does_not_become_our_service(self):
        self.state.write_text(json.dumps({'pid':123,'start':'OLD'}))
        with patch.object(self.module, 'STATE', self.state), patch.object(self.module, 'proc_info', return_value=self.valid), \
             patch.object(self.module, 'ProcessRef') as ref:
            self.assertIsNone(self.module.owned())
            ref.assert_not_called()
    def test_valid_identity_gets_a_pinned_reference(self):
        self.state.write_text(json.dumps({'pid':123,'start':'A'}))
        reference = Mock(); reference.alive.return_value=True
        with patch.object(self.module, 'STATE', self.state), patch.object(self.module, 'proc_info', return_value=self.valid), \
             patch.object(self.module, 'ProcessRef', return_value=reference):
            self.assertIs(self.module.owned(), reference)
    def test_stop_does_not_remove_an_unrelated_legacy_pidfile(self):
        self.pidfile.write_text('999\n')
        with patch.object(self.module, 'PIDFILE', self.pidfile), patch.object(self.module, 'owned', return_value=None):
            self.module.stop()
        self.assertEqual(self.pidfile.read_text(), '999\n')
    def test_stop_cleans_only_its_own_legacy_pidfile(self):
        self.pidfile.write_text('123\n')
        self.state.write_text('{}')
        ref = Mock(); ref.info={'pid':123}; ref.alive.return_value=False
        with patch.object(self.module, 'PIDFILE', self.pidfile), patch.object(self.module, 'STATE', self.state), \
             patch.object(self.module, 'owned', return_value=ref): self.module.stop()
        self.assertFalse(self.pidfile.exists())
        self.assertFalse(self.state.exists())
        ref.terminate.assert_called_once()
        ref.close.assert_called_once()
