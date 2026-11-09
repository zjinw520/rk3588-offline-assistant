"""One loaded model, one request at a time, explicit streaming and cancellation."""

import queue
import threading
import time
import uuid

from .prompts import LocalPrompts, validate_messages
from .runtime import NativeModel


class BusyError(RuntimeError):
    pass


class Job:
    def __init__(self, model):
        self.id = uuid.uuid4().hex
        self.model = model
        self.events = queue.Queue()
        self.done = threading.Event()
        self.cancelled = threading.Event()
        self.cancel_lock = threading.RLock()
        self.runtime = None
        self.error = None
        self.result = None
        self.started_ns = time.monotonic_ns()
        self.thread = None

    def cancel(self):
        with self.cancel_lock:
            if self.done.is_set():
                return
            self.cancelled.set()
            if self.runtime is not None:
                self.runtime.abort()

    def chunks(self, check=None):
        while not self.done.is_set() or not self.events.empty():
            if check is not None:
                check()
            try:
                yield self.events.get(timeout=0.2)
            except queue.Empty:
                continue


class OutputFilter:
    """Remove generation terminators across arbitrary callback boundaries."""
    TERMINATORS = ("<|im_end|>", "<|endoftext|>", "<｜end▁of▁sentence｜>")

    def __init__(self):
        self.pending = ""

    def feed(self, text, final=False):
        self.pending += text
        for token in self.TERMINATORS:
            self.pending = self.pending.replace(token, "")
        keep = 0
        if not final:
            for token in self.TERMINATORS:
                for size in range(1, min(len(token), len(self.pending) + 1)):
                    if self.pending.endswith(token[:size]):
                        keep = max(keep, size)
        end = len(self.pending) - keep
        output, self.pending = self.pending[:end], self.pending[end:]
        return output


class Engine:
    def __init__(self, settings, factory=NativeModel, prompts=None):
        self.settings = settings
        self.factory = factory
        self.prompts = prompts or LocalPrompts()
        self.gate = threading.Lock()
        self.state_lock = threading.RLock()
        self.loaded = None
        self.loaded_name = ""
        self.active = None
        self.closed = False
        self.verified_inference = False

    def start(self, name, messages):
        if not isinstance(name, str):
            raise ValueError("model must be a string")
        if name not in self.settings.models:
            raise KeyError("Unknown model")
        messages = validate_messages(messages)
        if not self.gate.acquire(blocking=False):
            raise BusyError("Another model request is running")
        with self.state_lock:
            if self.closed:
                self.gate.release()
                raise BusyError("Service is shutting down")
            job = Job(name)
            self.active = job
        job.thread = threading.Thread(target=self._work, args=(job, messages),
                                      name="local-model-" + job.id[:8], daemon=True)
        try:
            job.thread.start()
        except Exception:
            with self.state_lock:
                self.active = None
            self.gate.release()
            raise
        return job

    def _work(self, job, messages):
        try:
            model = self.settings.models[job.model]
            prompt = self.prompts.prepare(model, messages)
            if job.cancelled.is_set():
                raise RuntimeError("Request cancelled")
            load_started = time.monotonic_ns()
            if self.loaded_name != job.model:
                if self.loaded is not None:
                    self.loaded.close() # Finish unloading before loading the next model.
                    self.loaded, self.loaded_name = None, ""
                self.loaded = self.factory(self.settings, model)
                self.loaded_name = job.model
            load_duration = time.monotonic_ns() - load_started
            with job.cancel_lock:
                if job.cancelled.is_set():
                    raise RuntimeError("Request cancelled")
                job.runtime = self.loaded
            output = []
            total_bytes = [0]
            cleanup = OutputFilter()

            def emit(text):
                if job.cancelled.is_set():
                    return
                total_bytes[0] += len(text.encode("utf-8"))
                if total_bytes[0] > 1024 * 1024:
                    raise RuntimeError("Generated text exceeds the 1 MiB limit")
                visible = cleanup.feed(text)
                if visible:
                    output.append(visible)
                    job.events.put(visible)

            if prompt.prefix:
                emit(prompt.prefix)
            stats = self.loaded.run(prompt.tokens, emit, job.cancelled)
            tail = cleanup.feed("", final=True)
            if tail:
                output.append(tail)
                job.events.put(tail)
            if job.cancelled.is_set():
                raise RuntimeError("Request cancelled")
            job.result = dict(stats, text="".join(output),
                              total_duration=time.monotonic_ns() - job.started_ns,
                              load_duration=load_duration,
                              trimmed_messages=prompt.removed_messages,
                              done_reason="length" if stats.get("eval_count", 0) >= model.max_new_tokens else "stop")
            self.verified_inference = True
        except Exception as error:
            job.error = str(error)
        finally:
            # Cancel/abort cannot still be using the native handle when a
            # following request starts unloading or switching that handle.
            with job.cancel_lock:
                job.runtime = None
                with self.state_lock:
                    self.active = None
                    self.gate.release()
                    job.done.set()

    def cancel(self, request_id):
        with self.state_lock:
            job = self.active
        if job is None or job.id != request_id:
            return False
        job.cancel()
        return True

    def status(self):
        with self.state_lock:
            active = self.active
            return {"loaded_model": self.loaded_name,
                    "request_id": active.id if active else None,
                    "busy": active is not None,
                    "hardware_inference_verified": self.verified_inference}

    def unload(self):
        if not self.gate.acquire(blocking=False):
            raise BusyError("Cannot unload a running model")
        try:
            if self.loaded is not None:
                self.loaded.close()
                self.loaded, self.loaded_name = None, ""
        finally:
            self.gate.release()

    def close(self):
        with self.state_lock:
            self.closed = True
            active = self.active
        if active:
            active.cancel()
            active.thread.join(timeout=5)
            if not active.done.is_set():
                return False # Never free a native model while its run is live.
        self.unload()
        return True
