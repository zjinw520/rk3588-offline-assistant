"""Exercise actual compiled bridge against a fake SDK; no NPU involved."""
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

from local_ai.config import Model, Settings
from local_ai.runtime import NativeModel, RuntimeErrorLocal

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "win32" and (ROOT / "build/mock-rkllm.dll").is_file(),
                     "Windows native mock binaries not built")
class NativeTests(unittest.TestCase):
    def model(self):
        settings = Settings(ROOT / "build/mock-rkllm.dll", ROOT / "build/localai-bridge-test.dll", {})
        model = Model("test", Path("model"), Path("tokens"), max_new_tokens=128)
        with patch("local_ai.runtime.platform.system", return_value="Linux"), \
             patch("local_ai.runtime.platform.machine", return_value="aarch64"), \
             patch("local_ai.runtime.verify_runtime"):
            value = NativeModel(settings, model)
        self.addCleanup(value.close)
        return value
    def test_actual_callback_abi_and_split_utf8(self):
        model = self.model()
        chunks = []
        stats = model.run([42, 43, 44], chunks.append, threading.Event())
        self.assertEqual("".join(chunks), "你好")
        self.assertEqual(stats["prompt_eval_count"], 3)
        self.assertEqual(stats["eval_count"], 2)
        self.assertEqual(stats["prompt_eval_duration"], 12500000)
        self.assertEqual(stats["eval_duration"], 30000000)
    def test_sdk_error_is_propagated(self):
        with self.assertRaisesRegex(RuntimeErrorLocal, "inference error"):
            self.model().run([666], lambda text: None, threading.Event())
    def test_abort_does_not_free_active_handle(self):
        model, cancelled, errors = self.model(), threading.Event(), []
        def run():
            try: model.run([777], lambda text: None, cancelled)
            except RuntimeErrorLocal as error: errors.append(str(error))
        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(.1)
        with self.assertRaisesRegex(RuntimeErrorLocal, "destroy failed"):
            model.close()
        self.assertIsNotNone(model.handle)
        cancelled.set(); model.abort(); thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors)
        model.close()
        self.assertIsNone(model.handle)
