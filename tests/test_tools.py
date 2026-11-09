import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_ai.elf import inspect_elf
from local_ai.guard import require_exclusive_runtime, runtime_users

ROOT = Path(__file__).resolve().parents[1]


def tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ToolsTests(unittest.TestCase):
    def test_vendor_header_is_exact_official_blob(self):
        data = (ROOT / "third_party/rockchip-1.3.0/include/rkllm.h").read_bytes()
        git = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        self.assertEqual(git, "c76346c7f7bcab9e8eb61e3f903ee93cdc03d236")
    def test_model_import_uses_local_tokenizer_and_no_shell_evaluation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "qwen2-0.5b"
            (root / "tokenizer").mkdir(parents=True)
            (root / "tokenizer/tokenizer_config.json").write_text("{}")
            (root / "Modelfile").write_text('FROM="model.rkllm"\nHUGGINGFACE_PATH="remote/model"\nSYSTEM="hello $(touch bad)"\n', encoding="utf-8")
            prepare = tool("prepare_board")
            record = prepare.model_record(root)
            self.assertEqual(Path(record["tokenizer"]), root / "tokenizer")
            self.assertEqual(record["system"], "hello $(touch bad)")
            self.assertFalse((root / "bad").exists())
    def test_duplicate_vendor_copy_is_not_overwritten(self):
        prepare = tool("prepare_board")
        with tempfile.TemporaryDirectory() as folder:
            source, destination = Path(folder) / "src", Path(folder) / "dst"
            source.write_bytes(b"new"); destination.write_bytes(b"old")
            with self.assertRaises(ValueError): prepare.copy_vendor(source, destination)
            self.assertEqual(destination.read_bytes(), b"old")
    def test_guard_detects_other_runtime_process(self):
        with tempfile.TemporaryDirectory() as folder:
            proc = Path(folder)
            (proc / "123456").mkdir()
            (proc / "123456/maps").write_text("r-xp /some/path/librkllmrt.so")
            (proc / str(os.getpid())).mkdir()
            (proc / str(os.getpid()) / "maps").write_text("librkllmrt.so")
            self.assertEqual(runtime_users(proc), [123456])
        with patch("local_ai.guard.runtime_users", return_value=[42]):
            with self.assertRaisesRegex(RuntimeError, "42"): require_exclusive_runtime()
    def test_release_does_not_collect_private_data(self):
        names = tool("build_release").source_files()
        self.assertFalse(any("config.local" in n or "vendor-local" in n or n.endswith((".rkllm", ".wav", ".log", ".dll")) for n in names))
        self.assertIn("native/bridge.cpp", names)
    def test_package_verifier_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "a.py").write_bytes(b"original")
            expected = hashlib.sha256(b"original").hexdigest()
            (root / "PACKAGE_MANIFEST.json").write_text(json.dumps({"kind":"test", "files":{"a.py":expected}}))
            verifier = tool("verify_package")
            verifier.verify(root)
            (root / "a.py").write_bytes(b"changed")
            with self.assertRaises(ValueError): verifier.verify(root)
    @unittest.skipUnless((ROOT / "build/liblocalai-rkllm130.so").is_file(), "AArch64 artifact not built")
    def test_aarch64_bridge_needs_only_old_glibc_and_c_loader(self):
        elf = inspect_elf(ROOT / "build/liblocalai-rkllm130.so")
        self.assertEqual(elf["machine"], 183)
        self.assertLessEqual(tuple(map(int, elf["highest_glibc"].split("."))), (2, 37))
        self.assertNotIn("libstdc++.so.6", elf["needed"])
