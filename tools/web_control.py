"""Manage the documented local web service using verified Linux process identity."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from trial_board import ProcessRef, TrialError, proc_info, wait_for
from service_control import write_json

ENTRY = Path("/userdata/rkllama-web.py")
STATE = ROOT / "run/web-service.json"
OPENER = build_opener(ProxyHandler({}))


def expected(info):
    return info["argv"][1:] == ["-u", str(ENTRY)] and info["pid"] > 1


def owned():
    if not STATE.is_file():
        return None
    record = json.loads(STATE.read_text(encoding="utf-8"))
    if type(record.get("pid")) is not int or record["pid"] <= 1:
        raise TrialError("Invalid web service identity record")
    try:
        info = proc_info(record["pid"])
        if info["start"] != record.get("start") or not expected(info):
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
        print("LOCAL_WEB_ALREADY_RUNNING pid=" + str(ref.info["pid"]))
        ref.close()
        return
    python = Path(os.environ.get("LOCAL_AI_PYTHON", str(ROOT / ".venv/bin/python")))
    if not python.is_file() or not ENTRY.is_file():
        raise TrialError("Prepare the board Python environment and web file first")
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("0.0.0.0", 8081))
    with (ROOT / "web-service.log").open("ab", buffering=0) as log:
        child = subprocess.Popen([str(python), "-u", str(ENTRY)], cwd=str(ROOT),
                                 stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=subprocess.STDOUT, start_new_session=True)
    try:
        def ready():
            if child.poll() is not None:
                raise TrialError("Web process exited; inspect web-service.log")
            with OPENER.open("http://127.0.0.1:8081/api/status", timeout=2) as response:
                status = json.load(response)
            return status.get("online") is True and expected(proc_info(child.pid))
        wait_for(ready, 30, "Local web startup")
        info = proc_info(child.pid)
        write_json(STATE, {"pid": child.pid, "start": info["start"]})
    except BaseException:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        raise
    print("LOCAL_WEB_STARTED pid={} port=8081".format(child.pid))


def stop():
    ref = owned()
    if ref is None:
        print("LOCAL_WEB_NOT_RUNNING")
        return
    try:
        # Let this web process reap the voice child it created before exiting.
        request = Request("http://127.0.0.1:8081/api/voice/stop", data=b"{}",
                          headers={"Content-Type": "application/json"})
        with OPENER.open(request, timeout=8) as response:
            if json.load(response).get("ok") is not True:
                raise TrialError("Voice stop not confirmed; web process was kept")
        ref.terminate()
        wait_for(lambda: not ref.alive(), 10, "Local web stop")
        STATE.unlink(missing_ok=True)
        print("LOCAL_WEB_STOPPED")
    finally:
        ref.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "stop", "status"))
    args = parser.parse_args()
    import fcntl
    run = ROOT / "run"
    if run.is_symlink():
        raise TrialError("Refusing run-directory symlink")
    run.mkdir(mode=0o700, exist_ok=True)
    with (run / "web-service.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == "status":
            ref = owned()
            if ref is None:
                print("LOCAL_WEB_NOT_RUNNING")
                return 1
            print("LOCAL_WEB_RUNNING pid=" + str(ref.info["pid"]))
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
        print("LOCAL_WEB_ERROR " + str(error), file=sys.stderr)
        raise SystemExit(1)
