import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_ai.config import load_settings
from tools import chat, configure


class StandaloneConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'build').mkdir()
        (self.root / 'build/liblocalai-rkllm130.so').write_bytes(b'our-bridge')
        self.runtime = self.root / 'download.so'
        self.runtime.write_bytes(b'official-runtime-test-double')
        self.model = self.root / 'model.rkllm'
        self.model.write_bytes(b'model')
        self.tokenizer = self.root / 'tokenizer'
        self.tokenizer.mkdir()
        (self.tokenizer / 'tokenizer_config.json').write_text('{}')
        self.records = [('qwen2-0.5b', self.model, self.tokenizer)]
        self.addCleanup(patch.stopall)
        patch.object(configure, 'verify_runtime', return_value={'sha256': 'test-double'}).start()
        patch.object(configure, 'inspect_elf', return_value={'machine': 183}).start()

    def test_standalone_configuration_has_no_legacy_dependency_and_copies_vendor(self):
        gomp = self.root / 'libgomp.so.1'
        gomp.write_bytes(b'compatible-gomp')
        configure.configure(self.root, self.runtime, self.records, gomp)
        settings = load_settings(self.root / 'config.local.json')
        self.assertEqual(settings.models['qwen2-0.5b'].path, self.model)
        self.assertEqual(settings.runtime.read_bytes(), self.runtime.read_bytes())
        self.assertEqual((self.root / 'vendor-local/libgomp.so.1').read_bytes(), b'compatible-gomp')
        self.assertFalse((self.root / 'config.preparing.json').exists())

    def test_existing_configuration_is_never_overwritten(self):
        config = self.root / 'config.local.json'
        config.write_bytes(b'keep-me')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            configure.configure(self.root, self.runtime, self.records)
        self.assertEqual(config.read_bytes(), b'keep-me')

    def test_bad_model_settings_fail_before_vendor_copy(self):
        for records in [self.records * 2, [('invalid name', self.model, self.tokenizer)]]:
            with self.assertRaises(ValueError):
                configure.configure(self.root, self.runtime, records)
            self.assertFalse((self.root / 'config.local.json').exists())
            self.assertFalse((self.root / 'vendor-local').exists())
            self.assertFalse((self.root / 'config.preparing.json').exists())

    def test_failed_runtime_verification_does_not_create_configuration(self):
        with patch.object(configure, 'verify_runtime', side_effect=RuntimeError('wrong runtime')):
            with self.assertRaisesRegex(RuntimeError, 'wrong runtime'):
                configure.configure(self.root, self.runtime, self.records)
        self.assertFalse((self.root / 'config.local.json').exists())


class StreamingClientTests(unittest.TestCase):
    def response(self, events):
        return io.BytesIO(b''.join(json.dumps(event, ensure_ascii=False).encode() + b'\n' for event in events))

    def test_utf8_stream_requires_done_and_sends_expected_schema(self):
        response = self.response([{'message': {'content': '你好'}, 'done': False},
                                  {'message': {'content': '！'}, 'done': True}])
        output = io.StringIO()
        with patch.object(chat, 'urlopen', return_value=response) as opener:
            result = chat.chat('http://127.0.0.1:18080/', 'qwen2-0.5b', '你好', output)
        self.assertEqual(output.getvalue(), '你好！\n')
        self.assertTrue(result['done'])
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, 'http://127.0.0.1:18080/api/chat')
        self.assertEqual(json.loads(request.data)['messages'], [{'role': 'user', 'content': '你好'}])

    def test_truncated_stream_and_model_error_are_not_success(self):
        for events in [[{'message': {'content': 'partial'}, 'done': False}],
                       [{'error': 'runtime failed', 'done': False}]]:
            with patch.object(chat, 'urlopen', return_value=self.response(events)):
                with self.assertRaises(RuntimeError):
                    chat.chat('http://127.0.0.1:18080', 'qwen', 'hello', io.StringIO())
