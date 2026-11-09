"""Fresh, single-use desktop wake handoff and verified process identities."""

import json
import math
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


BASE = Path("/userdata/voice-assistant")
REQUEST_PATH = BASE / "desktop-wake-request.json"
SERVICE_STATE = BASE / "desktop-wake-state.json"


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic_json(path, value):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def process_info(pid):
    try:
        if type(pid) is not int or pid <= 1:
            return None
        directory = Path("/proc") / str(pid)
        fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] in ("Z", "X"):
            return None
        arguments = (directory / "cmdline").read_bytes().split(b"\0")
        return {"pid": pid, "start_time": fields[19], "arguments": arguments}
    except (OSError, IndexError, ValueError):
        return None


def process_matches(pid, start_time=None, script=None, argument=None):
    info = process_info(pid)
    if info is None or (start_time is not None and info["start_time"] != str(start_time)):
        return False
    if script is not None and os.fsencode(str(script)) not in info["arguments"]:
        return False
    return argument is None or os.fsencode(argument) in info["arguments"]


def foreground_active():
    try:
        pid = int((BASE / "voice-assistant.pid").read_text().strip())
    except (OSError, ValueError):
        pid = None
    if process_matches(pid, script=BASE / "voice_assistant.py"):
        return True
    request = read_json(REQUEST_PATH)
    return (request.get("status") == "claimed" and
            process_matches(request.get("worker_pid"), request.get("worker_start"),
                            script=BASE / "voice_assistant.py"))


def native_app_active():
    state = read_json(BASE / "qml-assistant-state.json")
    return (process_matches(state.get("supervisor_pid"), state.get("start_time"),
                            script=BASE / "rkllama-qml/launch.py") and
            process_matches(state.get("qmlscene_pid"), script=BASE / "rkllama-qml/rk3588-ai"))


def valid_pending(request, now=None):
    now = time.time() if now is None else now
    created = request.get("created")
    return (request.get("status") == "pending" and isinstance(request.get("token"), str)
            and len(request["token"]) == 32 and type(created) in (int, float)
            and math.isfinite(created) and 0 <= now - created <= 90
            and isinstance(request.get("question"), str) and len(request["question"]) <= 2048
            and process_matches(request.get("source_pid"), request.get("source_start"),
                                script=BASE / "desktop_wake.py", argument="--run"))


@contextmanager
def request_lock(path):
    import fcntl
    with Path(str(path) + ".lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def create_request(question, asr_seconds, settings, path=None):
    path = Path(path) if path is not None else REQUEST_PATH
    identity = process_info(os.getpid())
    if identity is None:
        raise RuntimeError("无法读取后台唤醒进程标识")
    value = {"token": uuid.uuid4().hex, "status": "pending", "created": time.time(),
             "source_pid": os.getpid(), "source_start": identity["start_time"],
             "question": question[:2048], "asr_seconds": float(asr_seconds),
             "model": settings["model"], "speaker": settings["speaker"], "speed": settings["speed"]}
    with request_lock(path):
        atomic_json(path, value)
    return value


def claim_request(path=None):
    path = Path(path) if path is not None else REQUEST_PATH
    with request_lock(path):
        value = read_json(path)
        if not valid_pending(value):
            return None
        info = process_info(os.getpid())
        if info is None:
            raise RuntimeError("无法读取对话进程标识")
        value.update(status="claimed", worker_pid=os.getpid(), worker_start=info["start_time"])
        atomic_json(path, value)
    return value


def fail_request(token, reason, path=None):
    path = Path(path) if path is not None else REQUEST_PATH
    with request_lock(path):
        value = read_json(path)
        if value.get("token") == token and value.get("status") == "pending":
            value.update(status="failed", error=reason)
            atomic_json(path, value)
