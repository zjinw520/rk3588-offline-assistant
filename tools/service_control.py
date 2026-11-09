"""Manage only this independent backend, using pidfd process identity checks."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from local_ai.config import load_settings
from local_ai.guard import require_exclusive_runtime
from local_ai.runtime import verify_runtime
from trial_board import ProcessRef, TrialError, json_get, proc_info, wait_for

PIDFILE = Path("/userdata/rkllama-lab/rkllama-server.pid")
STATE = ROOT / "run" / "model-service.json"


def write_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            os.chmod(temporary, 0o600)
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def expected(info):
    args = info["argv"]
    def pair(key, value):
        return any(args[i:i+2] == [key, value] for i in range(len(args)))
    return (pair("-m", "local_ai.server") and pair("--config", str(ROOT / "config.local.json"))
            and pair("--port", "8080") and "--enable-inference" in args)


def owned():
    if not STATE.is_file():
        return None
    state = json.loads(STATE.read_text())
    if type(state.get("pid")) is not int or state["pid"] <= 1:
        raise TrialError("Invalid independent service identity record")
    try:
        info = proc_info(state["pid"])
        if info["start"] != state.get("start") or not expected(info):
            return None
        ref = ProcessRef(info)
        if ref.alive():
            return ref
        ref.close()
    except (FileNotFoundError, ProcessLookupError):
        pass
    return None


def start():
    ref = owned()
    if ref:
        print("LOCAL_AI_ALREADY_RUNNING pid=" + str(ref.info["pid"]))
        ref.close()
        return
    settings = load_settings(ROOT / "config.local.json")
    verify_runtime(settings.runtime)
    require_exclusive_runtime()
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 8080))
    env = os.environ.copy()
    env["LOCAL_AI_PYTHON"] = env.get("LOCAL_AI_PYTHON", "/userdata/rkllama-venv/bin/python")
    with (ROOT / "model-service.log").open("ab", buffering=0) as log:
        child = subprocess.Popen(["/bin/sh", str(ROOT / "run_candidate.sh"), "--port", "8080", "--enable-inference"],
                                 cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    identity = None
    try:
        def ready():
            if child.poll() is not None:
                raise TrialError("Independent backend exited; inspect model-service.log")
            status = json_get("http://127.0.0.1:8080/healthz", timeout=2)
            info = proc_info(child.pid)
            return (status.get("backend") == "local-ai-independent" and status.get("inference_enabled") is True
                    and expected(info))
        wait_for(ready, 30, "Independent backend startup")
        identity = proc_info(child.pid)
        write_json(STATE, {"pid": child.pid, "start": identity["start"]})
        PIDFILE.write_text(str(child.pid) + "\n")
        print("LOCAL_AI_STARTED pid={} port=8080".format(child.pid))
    except BaseException:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)
        if identity is not None:
            STATE.unlink(missing_ok=True)
        raise


def stop():
    ref = owned()
    if ref is None:
        print("LOCAL_AI_NOT_RUNNING")
        return
    try:
        ref.terminate()
        wait_for(lambda: not ref.alive(), 20, "Independent backend stop")
        if PIDFILE.exists() and PIDFILE.read_text().strip() == str(ref.info["pid"]):
            PIDFILE.unlink()
        STATE.unlink(missing_ok=True)
        print("LOCAL_AI_STOPPED")
    finally:
        ref.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "stop", "status"))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    import fcntl
    run = ROOT / "run"
    if run.is_symlink():
        raise TrialError("Refusing service run-directory symlink")
    run.mkdir(mode=0o700, exist_ok=True)
    with (run / "service.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == "status":
            ref = owned()
            if ref is None:
                return 1
            if not args.quiet:
                print("LOCAL_AI_RUNNING pid=" + str(ref.info["pid"]))
            ref.close()
        elif args.command == "start":
            start()
        else:
            stop()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("LOCAL_AI_SERVICE_ERROR " + str(error), file=sys.stderr)
        raise SystemExit(1)
