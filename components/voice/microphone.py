"""Cooperate across standby and foreground recorders on the one ALSA device."""

import time
from contextlib import contextmanager
from pathlib import Path


MIC_LOCK = Path("/userdata/voice-assistant/microphone.lock")


@contextmanager
def microphone_lease(cancel=None, path=None, timeout=5.0):
    import fcntl
    target = Path(path) if path is not None else MIC_LOCK
    with target.open("a+") as lock:
        acquired = False
        started = time.monotonic()
        try:
            while True:
                if cancel is not None and cancel():
                    yield False
                    return
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if time.monotonic() - started >= timeout:
                        raise RuntimeError("麦克风仍被另一个助手录音进程占用")
                    time.sleep(0.05)
            yield True
        finally:
            if acquired:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
