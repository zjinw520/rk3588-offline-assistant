#!/usr/bin/env python3
"""Standby wake service; hand the microphone and first question to the native app."""

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import desktop_wake_state as state


BASE = state.BASE
SCRIPT = BASE / "desktop_wake.py"
LOG = BASE / "desktop-wake.log"
ENTRY = Path("/opt/ui/src/apps/rk3588-ai")
PYTHON = Path("/userdata/voice-venv/bin/python")
LIB = "/userdata/rkllama-lab/rkllama-83891fba4783dfe1dc93ce0168b8e826983c126e/src/rkllama/lib"
VERSION = "0.7.0"


def service_running():
    info = state.read_json(state.SERVICE_STATE)
    return info if state.process_matches(info.get("pid"), info.get("start_time"),
                                         script=SCRIPT, argument="--run") else None


def health(mode, error=""):
    info = state.process_info(os.getpid())
    if info is not None:
        state.atomic_json(state.SERVICE_STATE,
                          {"pid": os.getpid(), "start_time": info["start_time"],
                           "version": VERSION, "mode": mode, "error": error})


def settings():
    previous = state.read_json(BASE / "voice-control-state.json")
    model = previous.get("model", "qwen2-0.5b")
    if model not in ("qwen2-0.5b", "deepseek-r1-1.5b"):
        model = "qwen2-0.5b"
    speaker = previous.get("speaker", 2)
    if type(speaker) is not int or not 0 <= speaker <= 4:
        speaker = 2
    speed = previous.get("speed", 1.25)
    if type(speed) not in (int, float) or not 0.5 <= speed <= 2:
        speed = 1.25
    return {"model": model, "speaker": speaker, "speed": speed}


class AppHandoff:
    def __init__(self, stop, clock=None):
        self.stop = stop
        self.clock = clock or time.monotonic
        self.child = None

    def reap(self):
        if self.child is not None and self.child.poll() is not None:
            self.child.wait()
            self.child = None

    def open_app(self):
        if not ENTRY.is_file():
            raise RuntimeError("没有找到已经验证的原厂助手入口：" + str(ENTRY))
        self.reap()
        if self.child is None:
            with (BASE / "desktop-app-launch.log").open("ab") as log:
                self.child = subprocess.Popen(["/bin/sh", str(ENTRY)], stdin=subprocess.DEVNULL,
                                              stdout=log, stderr=subprocess.STDOUT,
                                              start_new_session=True)
            print("DESKTOP_APP_LAUNCH：打开原生助手。", flush=True)

    def wait(self, token, timeout=45):
        deadline = self.clock() + timeout
        next_launch = 0
        try:
            while not self.stop.is_set() and self.clock() < deadline:
                request = state.read_json(state.REQUEST_PATH)
                if request.get("token") != token:
                    return False
                if request.get("status") == "claimed":
                    print("DESKTOP_WAKE_HANDOFF_OK：应用已接管本次唤醒。", flush=True)
                    return True
                if request.get("status") != "pending":
                    return False
                if not state.native_app_active() and self.clock() >= next_launch:
                    self.open_app()
                    next_launch = self.clock() + 2
                self.stop.wait(0.2)
            return False
        finally:
            state.fail_request(token, "唤醒交接超时或后台已停止")


def standby_loop(voice, recognizer, stop, handoff):
    last_mode = None
    while not stop.is_set():
        handoff.reap()
        try:
            if state.foreground_active():
                if last_mode != "foreground":
                    print("DESKTOP_WAKE_PAUSED：应用正在对话，后台已释放麦克风。", flush=True)
                    health("foreground")
                    last_mode = "foreground"
                stop.wait(0.2)
                continue
            if last_mode != "standby":
                print("DESKTOP_WAKE_READY：在桌面说“你好小助手”即可打开助手。", flush=True)
                health("standby")
                last_mode = "standby"
            audio = voice.listen_for_utterance(
                "hw:CARD=rockchipes8388,DEV=0", voice.DEFAULT_VAD_MODEL, 0.5, 0.8, 20,
                cancel_check=lambda: stop.is_set() or state.foreground_active())
            if audio is None or stop.is_set() or state.foreground_active():
                continue
            text, elapsed = voice.transcribe_samples(recognizer, 16000, audio)
            if stop.is_set() or state.foreground_active():
                continue
            question = voice.wake_remainder(text)
            if question is None:
                if text:
                    print("桌面待唤醒识别：" + text, flush=True)
                continue
            request = state.create_request(question, elapsed, settings())
            print("DESKTOP_WAKE_DETECTED：即将打开助手，请等提示音后提问。", flush=True)
            health("handoff")
            last_mode = "handoff"
            if not handoff.wait(request["token"]):
                print("DESKTOP_WAKE_HANDOFF_FAILED：查看 desktop-app-launch.log 和 qml-assistant.log。",
                      flush=True)
                stop.wait(1)
        except Exception as error:
            print("DESKTOP_WAKE_RETRY：" + str(error), flush=True)
            health("retry", str(error))
            last_mode = "retry"
            stop.wait(3)


def run():
    import fcntl
    with (BASE / "desktop-wake.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("桌面唤醒已经在运行。", flush=True)
            return 0
        stop = threading.Event()
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, lambda *_: stop.set())
        health("initializing")
        try:
            import voice_assistant as voice
            print("加载桌面待机用的SenseVoice离线识别模型……", flush=True)
            recognizer = voice.create_recognizer(voice.DEFAULT_ASR_DIR, 2)
            standby_loop(voice, recognizer, stop, AppHandoff(stop))
            return 0
        except Exception as error:
            health("error", str(error))
            raise
        finally:
            if stop.is_set():
                health("stopped")
            print("DESKTOP_WAKE_STOPPED", flush=True)


def start():
    current = service_running()
    if current:
        print("DESKTOP_WAKE_ALREADY_RUNNING pid={} mode={}".format(current["pid"], current.get("mode")))
        return 0
    environment = os.environ.copy()
    environment["LD_LIBRARY_PATH"] = LIB + os.pathsep + environment.get("LD_LIBRARY_PATH", "")
    with LOG.open("ab") as log:
        child = subprocess.Popen([str(PYTHON), "-u", str(SCRIPT), "--run"], cwd=str(BASE),
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 env=environment, start_new_session=True)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        current = service_running()
        if child.poll() is not None and not current:
            raise RuntimeError("桌面唤醒启动失败，请查看 " + str(LOG))
        if current:
            if current.get("mode") in ("standby", "foreground", "handoff"):
                print("DESKTOP_WAKE_STARTED pid={}".format(current["pid"]))
                return 0
            if current.get("mode") in ("error", "retry"):
                raise RuntimeError("桌面唤醒未就绪：" + current.get("error", ""))
        time.sleep(0.2)
    raise RuntimeError("桌面唤醒20秒内未就绪，请查看 " + str(LOG))


def stop_service():
    current = service_running()
    if not current:
        print("DESKTOP_WAKE_NOT_RUNNING")
        return 0
    pid = current["pid"]
    if os.getpgid(pid) == pid:
        os.killpg(pid, signal.SIGTERM)
    else:
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not service_running():
            print("DESKTOP_WAKE_STOPPED")
            return 0
        time.sleep(0.1)
    latest = service_running()
    if latest and latest["pid"] == pid and latest["start_time"] == current["start_time"]:
        if os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    for name in ("run", "start", "stop", "status"):
        mode.add_argument("--" + name, action="store_true")
    args = parser.parse_args()
    if args.run:
        return run()
    if args.start:
        return start()
    if args.stop:
        return stop_service()
    current = service_running()
    print("DESKTOP_WAKE_STATUS " + str(current if current else "stopped"))
    return 0 if current else 1


# RK3588_FACTORY_HANDOFF_V1
from factory_launch import FactoryAppHandoff as AppHandoff
VERSION = '0.7.1-factory'

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("桌面唤醒失败：" + str(error), file=sys.stderr)
        raise SystemExit(1)
