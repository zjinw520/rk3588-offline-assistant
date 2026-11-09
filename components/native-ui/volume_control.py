"""SystemUI shared volume control and non-exclusive board volume-key monitor.

This helper lives only while the native assistant runs. The shared SystemUI
file is authoritative; the earlier ALSA Mixer remains available for diagnostics.
"""

import json
import os
import re
import select
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from system_volume import VolumeFile, factory_volume_keys_running


EVENT = struct.Struct("@llHHi")
KEY_STEPS = {114: -1, 115: 1}


def parse_mixer(text):
    limits = re.search(r"\bmin=(-?\d+),max=(-?\d+)", text)
    numbers = re.search(r"^\s*:\s*values=([\d,\s-]+)$", text, re.MULTILINE)
    if not limits or not numbers or "type=INTEGER," not in text:
        raise ValueError("无法解析 ES8388 PCM Volume，请检查 amixer cget 输出")
    minimum, maximum = (int(value) for value in limits.groups())
    values = [int(value.strip()) for value in numbers.group(1).strip().split(",")]
    if maximum <= minimum or not values or not all(minimum <= v <= maximum for v in values):
        raise ValueError("PCM Volume 的范围或声道数值异常")
    return {"minimum": minimum, "maximum": maximum, "values": values}


class Mixer:
    def __init__(self, card="rockchipes8388", step=6, runner=None, log=None):
        self.card = str(card)
        self.step = step
        self.runner = runner or subprocess.run
        self.log = log or (lambda text: None)
        self.lock = threading.RLock()
        self.key_device = ""
        self.key_error = ""

    def _command(self, args):
        env = dict(os.environ, LC_ALL="C")
        result = self.runner(
            ["amixer", "-c", self.card] + args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=2, check=False, env=env,
        )
        text = result.stdout.decode("utf-8", errors="replace")
        if result.returncode:
            raise RuntimeError("amixer 失败：" + text.strip()[:500])
        return text

    def _read(self):
        return parse_mixer(self._command(["cget", "name=PCM Volume"]))

    def _status(self, state):
        maximum_value = max(state["values"])
        percent = round(100 * (maximum_value - state["minimum"]) /
                        (state["maximum"] - state["minimum"]))
        return dict(state, percent=percent, step=self.step, key_device=self.key_device,
                    key_error=self.key_error, card=self.card)

    def status(self):
        with self.lock:
            return self._status(self._read())

    def adjust(self, direction, source="screen"):
        if type(direction) is not int or direction not in (-1, 1):
            raise ValueError("step 必须是 -1 或 1")
        with self.lock:
            state = self._read()
            values = [max(state["minimum"], min(state["maximum"],
                       value + direction * self.step)) for value in state["values"]]
            if values != state["values"]:
                self._command(["cset", "name=PCM Volume", ",".join(map(str, values))])
                state = self._read()
            self.log("source={} direction={} actual={}".format(source, direction, state["values"]))
            return self._status(state)


class SystemVolume:
    """Use the original SystemUI file, without changing PCM hardware gain."""
    def __init__(self, path=None, step=3, log=None, factory_detector=None, clock=None):
        self.file = VolumeFile(path) if path is not None else VolumeFile()
        self.step = step
        self.log = log or (lambda text: None)
        self.lock = threading.RLock()
        self.key_device = ""
        self.key_error = ""
        self.factory_detector = factory_detector or factory_volume_keys_running
        self.clock = clock or time.monotonic
        self.last_seen = None
        self.last_external_change = None

    def _read(self):
        current = self.file.read()
        if self.last_seen is not None and current != self.last_seen:
            self.last_external_change = self.clock()
        self.last_seen = current
        return current

    def _status(self, current):
        return {"minimum": 0, "maximum": 100, "values": [current],
                "percent": current, "step": self.step, "origin": "systemui",
                "path": str(self.file.path), "key_device": self.key_device,
                "key_error": self.key_error}

    def status(self):
        with self.lock:
            return self._status(self._read())

    def delegate_key_press(self):
        # When the factory key program is present, leave its first key step
        # alone. The assistant supplies missing holds through the same file.
        return self.factory_detector()

    def adjust(self, direction, source="screen"):
        if type(direction) is not int or direction not in (-1, 1):
            raise ValueError("step 必须是 -1 或 1")
        with self.lock:
            current = self._read()
            if (source == "key:hold" and self.factory_detector()
                    and self.last_external_change is not None
                    and self.clock() - self.last_external_change < 0.25):
                self.log("factory volume changed recently; skipping extra hold step")
                return self._status(current)
            target = max(0, min(100, current + direction * self.step))
            if target != current:
                self.file.write(target)
            self.last_seen = target
            self.log("source={} direction={} shared_percent={}".format(source, direction, target))
            return self._status(target)


class KeyPolicy:
    """Generate repeats in software, including for press/release-only keys."""
    def __init__(self, interval=0.3, delay=0.5):
        self.interval = interval
        self.delay = delay
        self.held = set()
        self.repeat_at = None

    def reset(self):
        self.held.clear()
        self.repeat_at = None

    def event(self, code, value, now):
        if code not in KEY_STEPS:
            return 0
        if value == 0:
            if code not in self.held:
                return 0
            self.held.remove(code)
            self.repeat_at = now + self.delay if len(self.held) == 1 else None
            return 0
        if value == 1:
            if code in self.held:
                return 0
            self.held.add(code)
            # Opposing keys held together cancel continuous adjustment.
            self.repeat_at = now + self.delay if len(self.held) == 1 else None
            return KEY_STEPS[code] if len(self.held) == 1 else 0
        # Ignore kernel value=2 repeats. Our timer is the only repeat source,
        # so enabling kernel repeat later cannot double the adjustment rate.
        return 0

    def tick(self, now):
        if len(self.held) != 1 or self.repeat_at is None or now < self.repeat_at:
            return 0
        self.repeat_at = now + self.interval
        # Do not replay missed ticks after a slow mixer operation.
        return KEY_STEPS[next(iter(self.held))]


def find_key_device():
    for entry in sorted(Path("/sys/class/input").glob("event*")):
        try:
            if (entry / "device/name").read_text().strip() == "adc-keys":
                return str(Path("/dev/input") / entry.name)
        except OSError:
            pass
    return None


class KeyMonitor(threading.Thread):
    def __init__(self, mixer):
        super().__init__(name="assistant-volume-keys", daemon=True)
        self.mixer = mixer
        self.stopped = threading.Event()

    def _adjust(self, direction, source):
        if direction and not self.stopped.is_set():
            try:
                if (source != "key:hold" and source.startswith("key:")
                        and getattr(self.mixer, "delegate_key_press", lambda: False)()):
                    self.mixer.log("{} first step delegated to sysvolume".format(source))
                    return
                self.mixer.adjust(direction, source)
            except Exception as error:
                self.mixer.log("key adjustment failed: {}".format(error))

    def _listen(self, descriptor):
        policy = KeyPolicy()
        pending = bytearray()
        dropping = False
        while not self.stopped.is_set():
            # Keep reading releases even when the driver sends no repeat
            # events; timer-only adjustment must not block input processing.
            ready, _, _ = select.select([descriptor], [], [], 0.05)
            if ready:
                try:
                    block = os.read(descriptor, EVENT.size * 32)
                except BlockingIOError:
                    block = None
                if block == b"":
                    raise RuntimeError("音量按键设备已断开")
                if block:
                    pending.extend(block)
                while len(pending) >= EVENT.size:
                    _, _, kind, code, value = EVENT.unpack(pending[:EVENT.size])
                    del pending[:EVENT.size]
                    if kind == 0 and code == 3:
                        dropping = True
                        policy.reset()
                        self.mixer.log("SYN_DROPPED: waiting for fresh key press")
                    elif dropping:
                        if kind == 0 and code == 0:
                            dropping = False
                    elif kind == 1:
                        direction = policy.event(code, value, time.monotonic())
                        self._adjust(direction, "key:{}".format(code))
            # Drain queued input before generating a timed repeat. In
            # particular, a queued release takes priority over another step.
            if not dropping and not self.stopped.is_set():
                queued, _, _ = select.select([descriptor], [], [], 0)
                if queued:
                    continue
                direction = policy.tick(time.monotonic())
                self._adjust(direction, "key:hold")

    def run(self):
        while not self.stopped.is_set():
            descriptor = None
            try:
                device = find_key_device()
                if device is None:
                    raise RuntimeError("没有找到 adc-keys，屏幕音量按钮仍可使用")
                descriptor = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
                self.mixer.key_device = device
                self.mixer.key_error = ""
                self.mixer.log("keys={} non-exclusive repeat=software delay=0.5 interval=0.3".format(device))
                self._listen(descriptor)
            except Exception as error:
                self.mixer.key_device = ""
                self.mixer.key_error = str(error)
                self.mixer.log("key monitor unavailable: {}".format(error))
            finally:
                if descriptor is not None:
                    os.close(descriptor)
            self.stopped.wait(3)


class VolumeService:
    def __init__(self, host="127.0.0.1", port=8082, log=None, mixer=None, keys=True,
                 control_path=None, desktop_path=None):
        self.mixer = mixer or SystemVolume(log=log)
        self.server = None
        self.thread = None
        self.monitor = None
        self.host = host
        self.port = port
        self.keys = keys
        self.control_path = Path(control_path) if control_path is not None else Path(
            "/userdata/voice-assistant/voice-control-state.json")
        self.desktop_path = Path(desktop_path) if desktop_path is not None else Path(
            "/userdata/voice-assistant/desktop-wake-request.json")

    def start(self):
        mixer = self.mixer
        control_path = self.control_path
        desktop_path = self.desktop_path

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def reply(self, status, value):
                raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == "/api/desktop-wake":
                    try:
                        value = json.loads(desktop_path.read_text(encoding="utf-8"))
                        created = value.get("created")
                        if (value.get("status") != "pending" or type(created) not in (int, float)
                                or not 0 <= time.time() - created <= 90):
                            value = {}
                        self.reply(200, value)
                    except (OSError, ValueError, AttributeError):
                        self.reply(200, {})
                    return
                if self.path == "/api/voice/control":
                    try:
                        state = json.loads(control_path.read_text(encoding="utf-8"))
                        self.reply(200, state if isinstance(state, dict) else {})
                    except (OSError, ValueError):
                        self.reply(200, {})
                    return
                if self.path != "/api/volume":
                    self.reply(404, {"error": "Not found"})
                    return
                try:
                    self.reply(200, mixer.status())
                except Exception as error:
                    self.reply(503, {"error": str(error)})

            def do_POST(self):
                if self.path != "/api/volume":
                    self.reply(404, {"error": "Not found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 128:
                        raise ValueError("无效的请求长度")
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(body, dict):
                        raise ValueError("请求必须是 JSON 对象")
                    result = mixer.adjust(body.get("step"))
                    self.reply(200, result)
                except (TypeError, ValueError) as error:
                    self.reply(400, {"error": str(error)})
                except Exception as error:
                    self.reply(503, {"error": str(error)})

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       name="assistant-volume-api", daemon=True)
        self.thread.start()
        if self.keys:
            self.monitor = KeyMonitor(self.mixer)
            self.monitor.start()
        return self

    def close(self):
        if self.monitor is not None:
            self.monitor.stopped.set()
            self.monitor.join(timeout=3)
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=1)
