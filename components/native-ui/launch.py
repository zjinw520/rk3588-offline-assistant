#!/usr/bin/env python3
"""Supervise one native QML assistant window without stopping SystemUI."""

import argparse
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

from ui_volume_service import VolumeService  # UI_REDESIGN_V080


APP_DIR = Path(__file__).resolve().parent
LOG_FILE = Path("/userdata/voice-assistant/qml-assistant.log")
STATE_FILE = Path("/userdata/voice-assistant/qml-assistant-state.json")
LOCK_FILE = Path("/tmp/rk3588-assistant-qml.lock")


def select_viewer(app_dir, from_systemui=False):
    native = app_dir / "rk3588-ai"
    if native.exists():
        with native.open("rb") as stream:
            header = stream.read(20)
        # ELF64, little endian, e_machine=EM_AARCH64. Do not accidentally run
        # an x86 host build or the factory shell entry at this path.
        if (len(header) < 20 or header[:6] != b"\x7fELF\x02\x01"
                or int.from_bytes(header[18:20], "little") != 183
                or not os.access(str(native), os.X_OK)):
            raise RuntimeError("rk3588-ai must be an executable AArch64 ELF")
        return str(native)
    if from_systemui:
        raise RuntimeError("SystemUI needs the compiled native-host/rk3588-ai host. "
                           "qmlscene alone does not implement its launch handshake.")
    viewer = shutil.which("qmlscene")
    if not viewer:
        raise RuntimeError("Neither rk3588-ai nor qmlscene was found")
    return viewer


def process_start(pid):
    try:
        # Field 22, after removing the command name in parentheses.
        tail = Path("/proc/{}/stat".format(pid)).read_text().rsplit(")", 1)[1].split()
        return tail[19]
    except (OSError, IndexError):
        return None


def stop_existing():
    try:
        state = json.loads(STATE_FILE.read_text())
        pid = int(state["supervisor_pid"])
        command = Path("/proc/{}/cmdline".format(pid)).read_bytes().split(b"\0")
        expected = str(APP_DIR / "launch.py").encode("utf-8")
        if pid <= 1 or expected not in command or process_start(pid) != state["start_time"]:
            raise ValueError("PID no longer belongs to this assistant")
        os.kill(pid, signal.SIGTERM)
        print("Stop requested for native assistant PID {}.".format(pid))
        return 0
    except (OSError, KeyError, TypeError, ValueError) as error:
        print("No verified native assistant to stop: {}".format(error))
        return 1


def stop_voice():
    request = Request("http://127.0.0.1:8081/api/voice/stop", data=b"{}",
                      headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=4) as response:
            response.read(4096)
    except Exception as error:
        print("Voice stop could not be confirmed: {}".format(error), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop", action="store_true", help="stop this native UI only")
    parser.add_argument("--check", action="store_true", help="check launcher prerequisites")
    parser.add_argument("--from-systemui", action="store_true",
                        help="require the native host's factory SystemUI handshake")
    args = parser.parse_args()
    if args.stop:
        return stop_existing()

    try:
        viewer = select_viewer(APP_DIR, args.from_systemui)
    except (OSError, RuntimeError) as error:
        print("Launcher check failed: {}".format(error), flush=True)
        return 1
    required = ("Main.qml", "ActionButton.qml", "Http.js", "volume_control.py", "system_volume.py")
    missing = [name for name in required if not (APP_DIR / name).is_file()]
    runtime = os.environ.get("XDG_RUNTIME_DIR", "/var/run")
    display = os.environ.get("WAYLAND_DISPLAY", "wayland-0")
    socket = Path(display) if display.startswith("/") else Path(runtime) / display
    print("window host={}".format(viewer), flush=True)
    print("Wayland socket={} exists={}".format(socket, socket.is_socket()), flush=True)
    print("QML files={}".format("OK" if not missing else missing), flush=True)
    if not viewer or missing or not socket.is_socket():
        print("Prerequisite check failed. SystemUI was not changed.", flush=True)
        return 1
    if args.check:
        print("Launcher prerequisites OK; QML module loading is checked on launch.")
        return 0

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock = LOCK_FILE.open("a+")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        # Normal factory hot launches are handled by the host's QtRO reply,
        # before SystemUI creates a process. Never accumulate blocked launchers.
        print("Native assistant is already running; duplicate launch ignored.", flush=True)
        return 0 if args.from_systemui else 2

    child = None
    volume_service = None
    requested_stop = [False]

    def handle_signal(signum, frame):
        requested_stop[0] = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGHUP, handle_signal)
    exit_code = 1
    try:
        # Rotate only the diagnostic log; application data and models stay put.
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 1024 * 1024:
            LOG_FILE.replace(LOG_FILE.with_suffix(".previous.log"))
        with LOG_FILE.open("a", encoding="utf-8", buffering=1) as log:
            log.write("\n{} LAUNCH v0.4 supervisor={} host={}\n".format(
                datetime.now().isoformat(), os.getpid(), viewer))

            def volume_log(message):
                try:
                    log.write("{} VOLUME {}\n".format(datetime.now().isoformat(), message))
                except ValueError:
                    pass  # A slow final mixer command may finish after log closure.

            volume_service = VolumeService(log=volume_log)
            try:
                volume_service.start()
                print("Volume buttons/key monitor: http://127.0.0.1:8082", flush=True)
            except Exception as error:
                volume_log("service unavailable: {}".format(error))
                volume_service.close()
                volume_service = None
                print("Volume control unavailable: {}".format(error), flush=True)
            child = subprocess.Popen(
                [viewer, str(APP_DIR / "Main.qml")], cwd=str(APP_DIR),
                stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True,
            )
            STATE_FILE.write_text(json.dumps({
                "supervisor_pid": os.getpid(), "qmlscene_pid": child.pid,
                "start_time": process_start(os.getpid()), "version": "0.4",
            }), encoding="utf-8")
            print("Native window PID={}. Log: {}".format(child.pid, LOG_FILE), flush=True)
            print("Use the on-screen exit button; Ctrl+C also stops this window.", flush=True)
            deadline = None
            while child.poll() is None:
                if requested_stop[0]:
                    if deadline is None:
                        try:
                            os.killpg(child.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        deadline = time.monotonic() + 3
                    elif time.monotonic() >= deadline:
                        try:
                            os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                try:
                    child.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    pass
            exit_code = child.returncode
            log.write("{} WINDOW_EXIT code={}\n".format(datetime.now().isoformat(), exit_code))
            if volume_service is not None:
                volume_service.close()
                volume_service = None
    finally:
        if volume_service is not None:
            volume_service.close()
        if child is not None:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                    child.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=2)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
            stop_voice()
        try:
            STATE_FILE.unlink()
        except FileNotFoundError:
            pass
        lock.close()
    print("Native assistant stopped; window host exit code={}.".format(exit_code), flush=True)
    if exit_code:
        print("Inspect: tail -n 80 {}".format(LOG_FILE), flush=True)
    return 0 if exit_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
