"""One paced ALSA stream per answer, with silence and soft clip edges."""

import subprocess
import tempfile
import time

import numpy as np

from system_volume import VolumeFile


class ContinuousPlayer:
    def __init__(self, device="plughw:CARD=rockchipes8388,DEV=0", volume=None, log=None,
                 player_factory=None, clock=None, sleep=None):
        self.device = device
        self.volume = volume or VolumeFile()
        self.log = log or (lambda message: None)
        self.factory = player_factory or subprocess.Popen
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.process = None
        self.errors = None
        self.rate = None
        self.gain = 0.0
        self.frames = 0
        self.started = None
        self.closed = False

    def _open(self, rate):
        if rate <= 0:
            raise ValueError("无效的播放采样率")
        self.rate = rate
        self.errors = tempfile.TemporaryFile()
        command = ["aplay", "-q", "-D", self.device, "-t", "raw", "-f", "S16_LE",
                   "-r", str(rate), "-c", "1", "--buffer-time=200000",
                   "--period-time=40000", "-"]
        self.process = self.factory(command, stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL, stderr=self.errors, bufsize=0)
        self.started = self.clock()
        self.gain = self.volume.gain()
        self.log("PCM连续播放器已打开：本轮各段共用一个音频流")
        self._silence(0.12)

    def _diagnostic(self):
        if self.errors is None:
            return ""
        self.errors.seek(0)
        return self.errors.read(8192).decode("utf-8", errors="replace").strip()

    def _write(self, samples):
        size = max(1, int(self.rate * 0.04))
        for offset in range(0, len(samples), size):
            block = samples[offset:offset + size]
            target = self.volume.gain()
            if abs(target - self.gain) > 0.0001:
                scaled = block * np.linspace(self.gain, target, len(block), dtype=np.float32)
            else:
                scaled = block * target
            self.gain = target
            pending = memoryview((np.clip(scaled, -1.0, 1.0) * 32767.0).astype("<i2").tobytes())
            try:
                while pending:
                    count = self.process.stdin.write(pending)
                    if count is None or count <= 0:
                        raise RuntimeError("播放器输入管道已经停止")
                    pending = pending[count:]
            except (OSError, RuntimeError) as error:
                raise RuntimeError("连续播放写入失败：{} {}".format(error, self._diagnostic())) from error
            self.frames += len(block)
            remaining = self.started + self.frames / self.rate - 0.12 - self.clock()
            if remaining > 0:
                self.sleep(remaining)

    def _silence(self, seconds):
        self._write(np.zeros(max(1, int(self.rate * seconds)), dtype=np.float32))

    def play(self, clip):
        if self.closed:
            raise RuntimeError("连续播放器已经关闭")
        if self.process is None:
            self._open(clip.sample_rate)
        elif clip.sample_rate != self.rate:
            raise RuntimeError("连续播放的采样率不能在回答中途改变")
        samples = np.asarray(clip.samples, dtype=np.float32).reshape(-1).copy()
        # Remove waveform jumps at sentence boundaries; leave saved answer.wav
        # and its original samples untouched. No hardware mixer is modified.
        edge = min(len(samples) // 2, max(2, int(self.rate * 0.008)))
        if edge >= 2:
            samples[:edge] *= np.linspace(0.0, 1.0, edge, dtype=np.float32)
            samples[-edge:] *= np.linspace(1.0, 0.0, edge, dtype=np.float32)
        self._write(samples)

    def idle(self):
        # Keep the PCM clock running while the next TTS segment is being made.
        # Before the first clip there is no device to keep alive.
        if self.process is not None and not self.closed:
            self._silence(0.04)

    def close(self, drain=True):
        if self.closed:
            return
        try:
            if self.process is not None:
                if drain:
                    self._silence(0.12)
                if self.process.stdin is not None and not self.process.stdin.closed:
                    self.process.stdin.close()
                result = self.process.wait(timeout=5)
                diagnostic = self._diagnostic()
                if result:
                    raise RuntimeError("连续播放器退出码={}：{}".format(result, diagnostic))
                if diagnostic:
                    self.log("ALSA播放提示：" + diagnostic)
        finally:
            self.closed = True
            if self.process is not None:
                if self.process.stdin is not None and not self.process.stdin.closed:
                    try:
                        self.process.stdin.close()
                    except OSError:
                        pass
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=2)
            if self.errors is not None:
                self.errors.close()
