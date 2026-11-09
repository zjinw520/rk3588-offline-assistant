"""Small ctypes client for our own stable bridge ABI, not SDK structures."""

import codecs
import ctypes as C
import hashlib
import platform
from pathlib import Path


OFFICIAL_RUNTIME_BLOB = "34167b32f99cb7ab19e21fb579fb750d96b878f1"
OFFICIAL_RUNTIME_SIZE = 7617472


class RuntimeErrorLocal(RuntimeError):
    pass


def runtime_fingerprint(path):
    path = Path(path)
    size = path.stat().st_size
    git = hashlib.sha1(b"blob " + str(size).encode("ascii") + b"\0")
    sha = hashlib.sha256()
    header = b""
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            if not header:
                header = block[:20]
            git.update(block)
            sha.update(block)
    return {"size": size, "git_blob": git.hexdigest(), "sha256": sha.hexdigest(),
            "aarch64_elf": len(header) >= 20 and header[:6] == b"\x7fELF\x02\x01"
            and int.from_bytes(header[18:20], "little") == 183}


def verify_runtime(path):
    info = runtime_fingerprint(path)
    if not (info["aarch64_elf"] and info["size"] == OFFICIAL_RUNTIME_SIZE
            and info["git_blob"] == OFFICIAL_RUNTIME_BLOB):
        raise RuntimeErrorLocal("Runtime is not the verified official RKLLM 1.3.0 binary; inspect it before loading")
    return info


class Event(C.Structure):
    _fields_ = [("state", C.c_int32), ("token_id", C.c_int32),
                ("text", C.c_void_p), ("text_len", C.c_size_t),
                ("prefill_ms", C.c_float), ("prefill_tokens", C.c_int32),
                ("generate_ms", C.c_float), ("generate_tokens", C.c_int32)]


Callback = C.CFUNCTYPE(None, C.c_void_p, C.POINTER(Event))


class NativeModel:
    def __init__(self, settings, model):
        if platform.system() != "Linux" or platform.machine() not in {"aarch64", "arm64"}:
            raise RuntimeErrorLocal("Real inference requires Linux AArch64")
        verify_runtime(settings.runtime)
        self.lib = C.CDLL(str(settings.bridge))
        self.lib.la_abi.restype = C.c_int32
        if self.lib.la_abi() != 1:
            raise RuntimeErrorLocal("Unsupported local bridge ABI")
        self.lib.la_open.argtypes = [C.c_char_p, C.c_char_p, C.c_int32, C.c_int32,
                                    C.c_float, C.c_void_p, C.c_size_t]
        self.lib.la_open.restype = C.c_void_p
        self.lib.la_run.argtypes = [C.c_void_p, C.POINTER(C.c_int32), C.c_size_t, Callback, C.c_void_p]
        self.lib.la_run.restype = C.c_int32
        self.lib.la_abort.argtypes = [C.c_void_p]
        self.lib.la_abort.restype = C.c_int32
        self.lib.la_close.argtypes = [C.c_void_p]
        self.lib.la_close.restype = C.c_int32
        error = C.create_string_buffer(2048)
        self.handle = self.lib.la_open(str(settings.runtime).encode(), str(model.path).encode(),
                                       model.context, model.max_new_tokens, model.temperature,
                                       error, len(error))
        if not self.handle:
            raise RuntimeErrorLocal(error.value.decode("utf-8", errors="replace"))

    def run(self, tokens, emit, cancelled):
        if cancelled.is_set():
            raise RuntimeErrorLocal("Request cancelled")
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        stats = {"prompt_eval_count": len(tokens), "eval_count": 0,
                 "prompt_eval_duration": 0, "eval_duration": 0}
        callback_error = []
        finished = [False]

        @Callback
        def receive(userdata, pointer):
            if callback_error:
                return
            try:
                event = pointer.contents
                if event.state == 3:
                    raise RuntimeErrorLocal("RKLLM reported an inference error")
                if event.state == 2:
                    finished[0] = True
                    stats.update(prompt_eval_count=max(0, event.prefill_tokens),
                                 eval_count=max(0, event.generate_tokens),
                                 prompt_eval_duration=max(0, int(event.prefill_ms * 1e6)),
                                 eval_duration=max(0, int(event.generate_ms * 1e6)))
                if event.text and event.text_len:
                    if event.text_len > 1024 * 1024:
                        raise RuntimeErrorLocal("Runtime text chunk is too large")
                    text = decoder.decode(C.string_at(event.text, event.text_len))
                    if text and not cancelled.is_set():
                        emit(text)
            except Exception as error:
                callback_error.append(error)

        array = (C.c_int32 * len(tokens))(*tokens)
        status = self.lib.la_run(self.handle, array, len(tokens), receive, None)
        if callback_error:
            raise callback_error[0]
        if cancelled.is_set():
            raise RuntimeErrorLocal("Request cancelled")
        if status != 0 or not finished[0]:
            raise RuntimeErrorLocal("Inference did not finish successfully (status {})".format(status))
        final = decoder.decode(b"", final=True)
        if final:
            emit(final)
        return stats

    def abort(self):
        if self.handle:
            self.lib.la_abort(self.handle)

    def close(self):
        if self.handle:
            status = self.lib.la_close(self.handle)
            if status != 0:
                raise RuntimeErrorLocal("Runtime destroy failed: {}".format(status))
            self.handle = None
