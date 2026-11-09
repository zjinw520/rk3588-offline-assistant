import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from local_ai.config import ConfigError, Model, Settings, load_settings
from local_ai.engine import BusyError, Engine, OutputFilter
from local_ai.prompts import LocalPrompts, Prompt, PromptError, validate_messages
from local_ai.runtime import RuntimeErrorLocal, runtime_fingerprint, verify_runtime


class FakeTokenizer:
    def apply_chat_template(self, messages, **kwargs):
        return "|".join(m["content"] for m in messages)
    def __call__(self, text, **kwargs):
        return {"input_ids": list(range(len(text)))}


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "model.rkllm").write_bytes(b"test")
        (self.root / "tokens").mkdir()
        (self.root / "tokens/tokenizer_config.json").write_text("{}")
        self.value = {"format": 1, "runtime": "rt.so", "bridge": "bridge.so", "models": [
            {"name": "test", "path": "model.rkllm", "tokenizer": "tokens"}]}
    def load(self):
        path = self.root / "config.json"
        path.write_text(json.dumps(self.value), encoding="utf-8")
        return load_settings(path)
    def test_relative_paths(self):
        self.assertEqual(self.load().models["test"].path, self.root / "model.rkllm")
    def test_empty_model_rejected(self):
        (self.root / "model.rkllm").write_bytes(b"")
        with self.assertRaises(ConfigError): self.load()
    def test_no_implicit_download(self):
        self.value["models"][0]["tokenizer"] = "Qwen/model"
        with self.assertRaises(ConfigError): self.load()
    def test_invalid_config_values(self):
        for key, value in [("context", True), ("max_new_tokens", -1), ("temperature", float("nan")), ("name", "../escape")]:
            with self.subTest(key=key):
                item = dict(self.value["models"][0])
                self.value["models"][0][key] = value
                with self.assertRaises(ConfigError): self.load()
                self.value["models"][0] = item
    def test_duplicate_names(self):
        self.value["models"].append(dict(self.value["models"][0]))
        with self.assertRaises(ConfigError): self.load()


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.model = Model("test", Path("model"), Path("tokens"), context=128, max_new_tokens=64, system="S")
        self.calls = []
        def loader(path, **kwargs):
            self.calls.append(kwargs)
            return FakeTokenizer()
        self.prompts = LocalPrompts(loader)
    def test_only_local_tokenizer_and_no_remote_code(self):
        prompt = self.prompts.prepare(self.model, [{"role": "user", "content": "hello"}])
        self.assertEqual(len(prompt.tokens), 7)
        self.assertEqual(self.calls, [{"local_files_only": True, "trust_remote_code": False}])
    def test_prune_complete_pairs_preserving_latest(self):
        messages = [{"role": "user", "content": "A"*40}, {"role": "assistant", "content": "B"*40},
                    {"role": "user", "content": "latest"}]
        prompt = self.prompts.prepare(self.model, messages)
        self.assertEqual(prompt.removed_messages, 2)
        self.assertEqual(len(messages), 3)
        self.assertEqual(len(prompt.tokens), 8)
    def test_oversize_latest_rejected(self):
        with self.assertRaises(PromptError):
            self.prompts.prepare(self.model, [{"role": "user", "content": "x"*100}])
    def test_reject_nontext_roles_and_invalid_order(self):
        for messages in [[{"role": [], "content": "a"}], [{"role": "assistant", "content": "a"}],
                         [{"role": "user", "content": "a", "images": []}],
                         [{"role": "user", "content": "a"}, {"role": "system", "content": "b"}]]:
            with self.subTest(messages=messages), self.assertRaises(PromptError): validate_messages(messages)
    def test_restore_deepseek_think_prefix(self):
        class R1(FakeTokenizer):
            def apply_chat_template(self, messages, **kwargs): return "question<think>\n"
        prompts = LocalPrompts(lambda *a, **k: R1())
        model = Model("deepseek-r1-1.5b", Path("m"), Path("t"))
        self.assertEqual(prompts.prepare(model, [{"role":"user","content":"hi"}]).prefix, "<think>")


class OutputTests(unittest.TestCase):
    def test_split_terminators_at_every_boundary(self):
        for token in OutputFilter.TERMINATORS:
            for index in range(len(token)+1):
                f = OutputFilter()
                result = f.feed("你好" + token[:index]) + f.feed(token[index:] + "再见") + f.feed("", final=True)
                self.assertEqual(result, "你好再见")
    def test_preserves_reasoning_and_comparison_text(self):
        f = OutputFilter()
        result = "".join(f.feed(c) for c in "<think>分析</think>结果x<3") + f.feed("", final=True)
        self.assertEqual(result, "<think>分析</think>结果x<3")
    def test_unknown_runtime_rejected_before_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "librkllmrt.so"
            path.write_bytes(b"fake")
            self.assertEqual(runtime_fingerprint(path)["size"], 4)
            with self.assertRaises(RuntimeErrorLocal): verify_runtime(path)


class StubPrompts:
    def prepare(self, model, messages): return Prompt([42])


class StubRuntime:
    def __init__(self, settings, model): self.name, self.closed = model.name, False
    def run(self, tokens, emit, cancelled):
        emit("你好<|im_"); emit("end|>")
        return {"eval_count": 2}
    def abort(self): pass
    def close(self): self.closed = True


class EngineTests(unittest.TestCase):
    def make_engine(self, factory=StubRuntime):
        settings = Settings(Path("rt"), Path("bridge"), {
            name: Model(name, Path("m"), Path("t")) for name in ("qwen", "deepseek")})
        engine = Engine(settings, factory, StubPrompts())
        self.addCleanup(engine.close)
        return engine
    def run_job(self, engine, model="qwen"):
        job = engine.start(model, [{"role": "user", "content": "hi"}])
        chunks = list(job.chunks())
        job.thread.join(1)
        return job, "".join(chunks)
    def test_switch_unloads_before_loading(self):
        objects = []
        def factory(settings, model):
            if objects: self.assertTrue(objects[-1].closed)
            objects.append(StubRuntime(settings, model))
            return objects[-1]
        engine = self.make_engine(factory)
        first, text = self.run_job(engine)
        self.assertIsNone(first.error)
        self.assertEqual(text, "你好")
        self.run_job(engine)
        self.assertEqual(len(objects), 1)
        self.run_job(engine, "deepseek")
        self.assertEqual(len(objects), 2)
        self.assertTrue(engine.status()["hardware_inference_verified"])
    def test_busy_rejected_then_abort_and_reuse(self):
        entered, released = threading.Event(), threading.Event()
        class Blocked(StubRuntime):
            def run(self, tokens, emit, cancelled):
                entered.set(); released.wait(2)
                if cancelled.is_set(): raise RuntimeError("cancelled")
                return {}
            def abort(self): released.set()
        engine = self.make_engine(Blocked)
        job = engine.start("qwen", [{"role":"user","content":"hi"}])
        self.assertTrue(entered.wait(1))
        with self.assertRaises(BusyError): engine.start("deepseek", [{"role":"user","content":"hi"}])
        with self.assertRaises(BusyError): engine.unload()
        self.assertFalse(engine.cancel("wrong"))
        self.assertTrue(engine.cancel(job.id))
        job.thread.join(2)
        self.assertTrue(job.done.is_set())
        self.assertIn("cancel", job.error)
        engine.unload()
        self.assertEqual(engine.status()["loaded_model"], "")
    def test_model_and_input_validation(self):
        engine = self.make_engine()
        with self.assertRaises(ValueError): engine.start([], [])
        with self.assertRaises(KeyError): engine.start("missing", [])
    def test_failure_is_not_success_and_retry_works(self):
        class Failed(StubRuntime):
            def run(self, tokens, emit, cancelled): emit("partial"); raise RuntimeError("model failed")
        engine = self.make_engine(Failed)
        job, text = self.run_job(engine)
        self.assertEqual(text, "partial")
        self.assertEqual(job.error, "model failed")
        self.assertIsNone(job.result)
        self.assertFalse(engine.status()["hardware_inference_verified"])
        engine.factory = StubRuntime
        engine.unload()
        job, _ = self.run_job(engine)
        self.assertIsNone(job.error)
