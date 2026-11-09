"""Minimal local HTTP API compatible with this project's existing clients."""

import argparse
from datetime import datetime, timezone
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import select
import signal
import socket
import threading
from urllib.parse import urlsplit

from . import __version__
from .config import load_settings
from .engine import BusyError, Engine
from .prompts import PromptError


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def chunk(job, text="", done=False):
    value = {"model": job.model, "created_at": now(), "request_id": job.id,
             "message": {"role": "assistant", "content": text}, "done": done}
    if done:
        value.update({key: val for key, val in job.result.items() if key != "text"})
    return value


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, engine, inference_enabled=False):
        self.engine = engine
        self.inference_enabled = inference_enabled
        self.slots = threading.BoundedSemaphore(12)
        super().__init__(address, Handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.0 503 Busy\r\nContent-Length: 0\r\n\r\n")
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalAI/" + __version__

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, fmt, *args):
        # Avoid persisting submitted text, model output or user query strings.
        pass

    @property
    def engine(self):
        return self.server.engine

    def reply(self, status, value):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/version":
            return self.reply(200, {"version": __version__, "backend": "local-ai-independent"})
        if path == "/healthz":
            return self.reply(200, dict(backend="local-ai-independent", version=__version__,
                                       inference_enabled=self.server.inference_enabled, **self.engine.status()))
        if path in ("/api/tags", "/api/status"):
            models = self.engine.settings.models
            if path == "/api/status":
                return self.reply(200, {"online": bool(models), "models": list(models), "model": next(iter(models), "")})
            try:
                tags = [{"name": name, "model": name, "size": model.path.stat().st_size,
                         "details": {"format": "rkllm"}} for name, model in models.items()]
            except OSError:
                return self.reply(503, {"error": "A configured model file is unavailable"})
            return self.reply(200, {"models": tags})
        if path == "/api/ps":
            return self.reply(200, self.engine.status())
        return self.reply(404, {"error": "Unknown API path"})

    def body(self):
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Chunked request bodies are not supported")
        length = self.headers.get("Content-Length", "")
        if not length.isdigit() or not 0 < int(length) <= 1024 * 1024:
            raise ValueError("Content-Length must be 1..1048576")
        raw = self.rfile.read(int(length))
        if len(raw) != int(length):
            raise ValueError("Incomplete request body")
        def invalid_constant(value):
            raise ValueError("Non-finite JSON value: " + value)
        value = json.loads(raw, parse_constant=invalid_constant)
        if not isinstance(value, dict):
            raise ValueError("Request must be a JSON object")
        return value

    def disconnected(self):
        readable, _, _ = select.select([self.connection], [], [], 0)
        return bool(readable and self.connection.recv(1, socket.MSG_PEEK) == b"")

    def do_POST(self):
        job = None
        started_stream = False
        try:
            value = self.body()
            path = urlsplit(self.path).path
            if path == "/api/cancel":
                token = value.get("request_id")
                if not isinstance(token, str) or len(token) != 32:
                    raise ValueError("A valid request_id is required")
                return self.reply(200, {"cancelled": self.engine.cancel(token)})
            if path == "/api/unload":
                self.engine.unload()
                return self.reply(200, {"unloaded": True})
            if path != "/api/chat":
                return self.reply(404, {"error": "Unknown API path"})
            if not self.server.inference_enabled:
                return self.reply(409, {"error": "Metadata-only mode; inference was not enabled"})
            if set(value) - {"model", "messages", "stream", "enable_thinking"}:
                raise ValueError("Unsupported chat option")
            stream = value.get("stream", False)
            if type(stream) is not bool or ("enable_thinking" in value and type(value["enable_thinking"]) is not bool):
                raise ValueError("stream/enable_thinking must be booleans")
            if value.get("enable_thinking") is False:
                raise ValueError("This backend does not support disabling model reasoning")
            job = self.engine.start(value.get("model"), value.get("messages"))
            if stream:
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Request-ID", job.id)
                self.end_headers()
                started_stream = True
            def check_client():
                if self.disconnected():
                    raise ConnectionError("Client disconnected")
            for text in job.chunks(check=check_client):
                if stream:
                    self.wfile.write(json.dumps(chunk(job, text), ensure_ascii=False).encode("utf-8") + b"\n")
                    self.wfile.flush()
            if job.error:
                if stream:
                    self.wfile.write(json.dumps({"error": job.error, "done": False}).encode() + b"\n")
                else:
                    self.reply(500, {"error": job.error})
                return
            final = chunk(job, "" if stream else job.result["text"], done=True)
            if stream:
                self.wfile.write(json.dumps(final, ensure_ascii=False).encode("utf-8") + b"\n")
                self.wfile.flush()
            else:
                self.reply(200, final)
        except KeyError:
            self.reply(404, {"error": "Unknown model"})
        except BusyError as error:
            self.reply(409, {"error": str(error)})
        except (ValueError, UnicodeError, PromptError) as error:
            if not started_stream:
                self.reply(400, {"error": str(error)})
        except (OSError, ConnectionError):
            pass
        finally:
            if job is not None and not job.done.is_set():
                job.cancel()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--enable-inference", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be 1024..65535")
    from .runtime import NativeModel
    from .guard import require_exclusive_runtime
    def guarded_factory(settings, model):
        require_exclusive_runtime()
        return NativeModel(settings, model)
    if args.enable_inference:
        require_exclusive_runtime()
    engine = Engine(load_settings(args.config), factory=guarded_factory)
    server = LocalServer(("127.0.0.1", args.port), engine, args.enable_inference)
    def stop(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print("LOCAL_AI_READY version={} http://127.0.0.1:{} (models load on request)".format(__version__, args.port), flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        engine.close()


if __name__ == "__main__":
    main()
