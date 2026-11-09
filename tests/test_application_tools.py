import ast
import io
import json
from pathlib import Path
import re
import socket
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_tools import tool

ROOT = Path(__file__).resolve().parents[1]


class WebControlTests(unittest.TestCase):
    def setUp(self):
        self.module = tool('web_control')
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / 'web-state.json'
        self.info = {'pid': 123, 'start': 'new',
                     'argv': ['python', '-u', str(self.module.ENTRY)]}

    def test_pid_reuse_or_other_script_is_never_adopted(self):
        for record, info in [({'pid': 123, 'start': 'old'}, self.info),
                             ({'pid': 123, 'start': 'new'}, dict(self.info, argv=['python', 'other.py']))]:
            self.state.write_text(json.dumps(record))
            with patch.object(self.module, 'STATE', self.state), \
                 patch.object(self.module, 'proc_info', return_value=info), \
                 patch.object(self.module, 'ProcessRef') as ref:
                self.assertIsNone(self.module.owned())
                ref.assert_not_called()

    def test_stop_unowned_service_does_not_contact_or_signal_any_process(self):
        with patch.object(self.module, 'owned', return_value=None), \
             patch.object(self.module.OPENER, 'open') as opener:
            self.module.stop()
        opener.assert_not_called()

    def test_failed_voice_stop_preserves_web_process_and_identity(self):
        self.state.write_text('{}')
        ref = Mock()
        with patch.object(self.module, 'STATE', self.state), \
             patch.object(self.module, 'owned', return_value=ref), \
             patch.object(self.module.OPENER, 'open', side_effect=OSError('no response')):
            with self.assertRaises(OSError):
                self.module.stop()
        ref.terminate.assert_not_called()
        ref.close.assert_called_once()
        self.assertTrue(self.state.exists())

    def test_confirmed_voice_stop_allows_pinned_web_stop(self):
        self.state.write_text('{}')
        ref = Mock()
        ref.alive.return_value = False
        with patch.object(self.module, 'STATE', self.state), \
             patch.object(self.module, 'owned', return_value=ref), \
             patch.object(self.module.OPENER, 'open', return_value=io.BytesIO(b'{"ok":true}')):
            self.module.stop()
        ref.terminate.assert_called_once()
        ref.close.assert_called_once()
        self.assertFalse(self.state.exists())

    @unittest.skipUnless(sys.platform == 'linux' and hasattr(__import__('os'), 'pidfd_open'),
                         'Real pidfd lifecycle requires Linux')
    def test_real_linux_start_ready_and_stop(self):
        import os
        with socket.socket() as probe:
            try:
                probe.bind(('0.0.0.0', 8081))
            except OSError:
                self.skipTest('Port 8081 already occupied')
        folder = Path(self.temporary.name)
        entry = folder / 'web_fixture.py'
        entry.write_text('''from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"online":true}')
    def do_POST(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')
    def log_message(self, *args): pass
HTTPServer(('127.0.0.1', 8081), Handler).serve_forever()
''')
        pid = None
        with patch.object(self.module, 'ROOT', folder), patch.object(self.module, 'ENTRY', entry), \
             patch.object(self.module, 'STATE', self.state), \
             patch.dict(os.environ, {'LOCAL_AI_PYTHON': sys.executable}):
            try:
                self.module.start()
                record = json.loads(self.state.read_text())
                pid = record['pid']
                ref = self.module.owned()
                self.assertIsNotNone(ref)
                ref.close()
                self.module.stop()
                self.assertFalse(self.state.exists())
            finally:
                if self.state.exists():
                    self.module.stop()
                if pid:
                    try:
                        os.waitpid(pid, 0)
                    except ChildProcessError:
                        pass


class FactoryStartupTests(unittest.TestCase):
    def setUp(self):
        self.module = tool('patch_factory_startup')
        self.original = '#!/bin/sh\nstart() {\n  /opt/ui/src/apps/sysvolume >/dev/null 2>&1 &\n  /opt/ui/systemui > /dev/null 2>&1 &\n}\n'

    def test_only_factory_process_receives_preload_and_existing_preloads_survive(self):
        changed = self.module.patched(self.original, '/userdata/voice-assistant/libsystemuiwake.so')
        self.assertIn('  /opt/ui/src/apps/sysvolume >/dev/null 2>&1 &\n', changed)
        self.assertIn('${LD_PRELOAD:+:$LD_PRELOAD}', changed)
        self.assertIn('/opt/ui/systemui > /dev/null 2>&1 &\n', changed)
        self.assertEqual(changed.count('RK3588_SYSTEMUI_WAKE_BRIDGE=1'), 1)

    def test_already_patched_ambiguous_or_unknown_launch_is_rejected(self):
        examples = [self.original * 2, self.original.replace('/opt/ui/systemui', '$APP'),
                    self.original + '# RK3588_SYSTEMUI_WAKE_BRIDGE=1\n']
        for text in examples:
            with self.assertRaises(ValueError):
                self.module.patched(text, '/userdata/libsystemuiwake.so')

    def test_rollback_restores_exact_original_and_rejects_concurrent_change(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            backup = base / 'backups/factory-startup-test'
            backup.mkdir(parents=True)
            target = base / 'S50systemui'
            original = backup / 'S50systemui'
            original.write_bytes(self.original.encode())
            target.write_text(self.module.patched(self.original, '/userdata/libsystemuiwake.so'))
            record = {'original': self.module.digest(original), 'installed': self.module.digest(target)}
            (backup / 'manifest.json').write_text(json.dumps(record))
            with patch.object(self.module, 'BASE', base), patch.object(self.module, 'TARGET', target):
                target.write_text('changed by another administrator')
                with self.assertRaisesRegex(ValueError, 'modified'):
                    self.module.rollback(backup)
                target.write_text(self.module.patched(self.original, '/userdata/libsystemuiwake.so'))
                self.module.rollback(backup)
                self.assertEqual(target.read_bytes(), original.read_bytes())


class TutorialTests(unittest.TestCase):
    def test_tutorial_python_blocks_compile_on_python310(self):
        count = 0
        for path in (ROOT / 'docs').glob('*.md'):
            text = path.read_text(encoding='utf-8')
            for script in re.findall(r"python[^\n]* - <<'PY'\n(.*?)\nPY", text, re.S):
                ast.parse(script, filename=str(path), feature_version=(3, 10))
                count += 1
        self.assertGreaterEqual(count, 12)

    def test_local_markdown_links_exist(self):
        paths = [ROOT / 'README.md', ROOT / 'components/README.md', *(ROOT / 'docs').glob('*.md')]
        for path in paths:
            for target in re.findall(r'(?<!!)\[[^\]]*\]\(([^)]+)\)', path.read_text(encoding='utf-8')):
                if '://' in target or target.startswith('#'):
                    continue
                self.assertTrue((path.parent / target.split('#')[0]).exists(), (path.name, target))
