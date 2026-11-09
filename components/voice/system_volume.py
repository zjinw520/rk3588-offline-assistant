"""The board's shared 0..100 volume and live-gain PCM playback.

Deploy this file beside launch.py and beside voice_assistant.py. The same
source is used by both processes; it has no Qt or model-runtime dependency.
"""

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path


SYSTEM_VOLUME_FILE = Path("/opt/ui/src/apps/resource/volume")


class VolumeFile:
    def __init__(self, path=SYSTEM_VOLUME_FILE):
        self.path = Path(path)
        self.last_good = None

    def read(self):
        # Original Qt writers truncate this small file in place. Retry a
        # transient empty read rather than interpreting it as mute or full.
        last_error = None
        for attempt in range(3):
            try:
                text = self.path.read_text(encoding="ascii").strip()
                if not re.fullmatch(r"\d{1,3}", text):
                    raise ValueError("原厂音量文件不是 0～100 整数：{!r}".format(text))
                value = int(text)
                if not 0 <= value <= 100:
                    raise ValueError("原厂音量超出 0～100：{}".format(value))
                self.last_good = value
                return value
            except (OSError, UnicodeError, ValueError) as error:
                last_error = error
                if attempt < 2:
                    time.sleep(0.01)
        raise RuntimeError("无法读取系统共享音量：{}".format(last_error))

    def write(self, percent):
        if type(percent) is not int or not 0 <= percent <= 100:
            raise ValueError("系统音量必须是 0～100 整数")
        # Preserve the inode because the original Qt applications watch it
        # with QFileSystemWatcher. Do not replace it using rename().
        with self.path.open("r+", encoding="ascii") as stream:
            stream.seek(0)
            stream.write(str(percent))
            stream.truncate()
            stream.flush()
        self.last_good = percent

    def gain(self):
        try:
            return self.read() / 100.0
        except RuntimeError:
            # A momentary partial write must not create an audible jump.
            return self.last_good / 100.0 if self.last_good is not None else 0.0


def factory_volume_keys_running():
    for directory in Path("/proc").glob("[0-9]*"):
        try:
            if (directory / "comm").read_text().strip() == "sysvolume":
                return True
        except OSError:
            pass
    return False


def play_pcm_with_volume(samples, sample_rate, device, volume=None,
                         player_factory=None, clock=None, sleep=None):
    """Play mono float samples, refreshing shared gain every 40ms block.

    Pace writes so an entire long answer is not pre-scaled and buffered before
    the user gets a chance to press the volume keys.
    """
    import numpy as np

    volume = volume or VolumeFile()
    player_factory = player_factory or subprocess.Popen
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    block_size = max(1, int(sample_rate * 0.04))
    gain = volume.gain()
    process = None
    command = ["aplay", "-q", "-D", device, "-t", "raw", "-f", "S16_LE",
               "-r", str(sample_rate), "-c", "1", "-"]
    with tempfile.TemporaryFile() as errors:
        try:
            process = player_factory(command, stdin=subprocess.PIPE,
                                     stdout=subprocess.DEVNULL, stderr=errors, bufsize=0)
            started = clock()
            for offset in range(0, len(samples), block_size):
                block = samples[offset:offset + block_size]
                target = volume.gain()
                if abs(target - gain) > 0.0001:
                    # Smooth gain changes over one block to avoid a click.
                    scaled = block * np.linspace(gain, target, len(block), dtype=np.float32)
                else:
                    scaled = block * target
                gain = target
                raw = (np.clip(scaled, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
                pending = memoryview(raw)
                while pending:
                    count = process.stdin.write(pending)
                    if count is None or count <= 0:
                        raise RuntimeError("扬声器播放管道已停止")
                    pending = pending[count:]
                # Permit a small prebuffer, without queueing seconds of audio.
                remaining = started + (offset + len(block)) / sample_rate - 0.12 - clock()
                if remaining > 0:
                    sleep(remaining)
            process.stdin.close()
            result = process.wait(timeout=5)
            if result:
                errors.seek(0)
                message = errors.read(4096).decode("utf-8", errors="replace").strip()
                raise RuntimeError("扬声器播放失败，退出码={}：{}".format(result, message))
        finally:
            if process is not None:
                if process.stdin is not None and not process.stdin.closed:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
