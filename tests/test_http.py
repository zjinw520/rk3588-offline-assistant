import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from local_ai.config import Model, Settings
from local_ai.engine import Engine
from local_ai.server import LocalServer
from test_core import StubPrompts, StubRuntime


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        model = Path(self.temp.name) / "model.rkllm"
        model.write_bytes(b"mock")
        self.settings = Settings(Path("rt"), Path("bridge"), {"qwen": Model("qwen", model, Path("t"))})
        self.engine = Engine(self.settings, StubRuntime, StubPrompts())
        self.server = LocalServer(("127.0.0.1", 0), self.engine, inference_enabled=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.engine.close()
        self.thread.join(1)
    def connection(self):
        return http.client.HTTPConnection(*self.server.server_address, timeout=3)
    def request(self, method, path, value=None):
        connection = self.connection()
        try:
            body = json.dumps(value).encode() if value is not None else None
            connection.request(method, path, body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()
    def chat(self, **kwargs):
        return dict(model="qwen", messages=[{"role":"user","content":"你好"}], **kwargs)
    def test_metadata_lists_existing_models_without_inference(self):
        status, body = self.request("GET", "/api/tags")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["models"][0]["name"], "qwen")
        self.assertIsNone(self.engine.loaded)
        status, body = self.request("GET", "/healthz")
        self.assertFalse(json.loads(body)["hardware_inference_verified"])
    def test_metadata_only_blocks_chat(self):
        self.server.inference_enabled = False
        status, _ = self.request("POST", "/api/chat", self.chat())
        self.assertEqual(status, 409)
        self.assertIsNone(self.engine.loaded)
    def test_nonstream_shape_for_existing_web(self):
        status, body = self.request("POST", "/api/chat", self.chat(stream=False))
        value = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIs(value["done"], True)
        self.assertEqual(value["message"]["content"], "你好")
    def test_ndjson_shape_for_existing_voice(self):
        status, body = self.request("POST", "/api/chat", self.chat(stream=True))
        lines = [json.loads(line) for line in body.splitlines()]
        self.assertEqual(status, 200)
        self.assertEqual("".join(line["message"]["content"] for line in lines), "你好")
        self.assertTrue(lines[-1]["done"])
        self.assertEqual(sum(line["done"] is True for line in lines), 1)
    def test_failure_has_no_false_success_marker(self):
        class Broken(StubRuntime):
            def run(self, tokens, emit, cancelled): emit("partial"); raise RuntimeError("test failure")
        self.engine.factory = Broken
        status, body = self.request("POST", "/api/chat", self.chat(stream=True))
        lines = [json.loads(line) for line in body.splitlines()]
        self.assertIn("error", lines[-1])
        self.assertFalse(any(line.get("done") is True for line in lines))
    def test_malformed_model_and_unsupported_options_return_errors(self):
        for key, val in [("model", []), ("messages", [{"role":[],"content":"a"}]),
                         ("stream", "yes"), ("images", []), ("enable_thinking", False)]:
            value = self.chat(); value[key] = val
            status, _ = self.request("POST", "/api/chat", value)
            self.assertEqual(status, 400, (key, val))
        value = self.chat(); value["model"] = "missing"
        self.assertEqual(self.request("POST", "/api/chat", value)[0], 404)
    def test_concurrent_request_busy_and_cancel(self):
        entered, released = threading.Event(), threading.Event()
        class Slow(StubRuntime):
            def run(self, tokens, emit, cancelled):
                entered.set(); released.wait(2)
                if cancelled.is_set(): raise RuntimeError("cancelled")
                return {}
            def abort(self): released.set()
        self.engine.factory = Slow
        connection = self.connection()
        connection.request("POST", "/api/chat", json.dumps(self.chat(stream=True)))
        response = connection.getresponse()
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(self.request("POST", "/api/chat", self.chat())[0], 409)
            _, body = self.request("GET", "/healthz")
            token = json.loads(body)["request_id"]
            status, body = self.request("POST", "/api/cancel", {"request_id": token})
            self.assertTrue(json.loads(body)["cancelled"])
            self.assertIn(b"cancelled", response.read())
        finally:
            connection.close(); released.set()
    def test_disconnect_before_first_token_aborts(self):
        entered, aborted = threading.Event(), threading.Event()
        class Silent(StubRuntime):
            def run(self, tokens, emit, cancelled):
                entered.set(); aborted.wait(2)
                raise RuntimeError("cancelled")
            def abort(self): aborted.set()
        self.engine.factory = Silent
        connection = self.connection()
        connection.request("POST", "/api/chat", json.dumps(self.chat(stream=True)))
        response = connection.getresponse()
        self.assertTrue(entered.wait(1))
        response.close(); connection.close()
        self.assertTrue(aborted.wait(1.5))
