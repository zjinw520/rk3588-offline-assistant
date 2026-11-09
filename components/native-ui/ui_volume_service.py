"""Add a read-only conversation view to the existing local volume service.

The original service still owns its server, audio-key monitor and lifetime.
All existing request handling is inherited unchanged. This module adds no
listener, process, audio operation, file write or third-party dependency.
"""

import re
import threading
import time
from pathlib import Path

from conversation_state import LOG_START, parse_log, read_snapshot
from volume_control import VolumeService as BaseVolumeService


VOICE_DIRECTORY = "/userdata/voice-assistant"
PYTHON_SCRIPT = VOICE_DIRECTORY + "/voice_assistant.py"
SHELL_SCRIPT = VOICE_DIRECTORY + "/run_voice_assistant.sh"


def _script_command(arguments):
    """Recognise a script execution, not a filename hidden in another argv."""
    if not arguments:
        return None
    command = arguments[0].rsplit("/", 1)[-1]
    remainder = arguments[1:]
    if command == "busybox" and remainder and remainder[0] in {"sh", "ash"}:
        command, remainder = remainder[0], remainder[1:]
    if re.fullmatch(r"python(?:[23](?:\.\d+)?)?", command):
        expected = PYTHON_SCRIPT
        options = {"-u", "-B", "-E", "-s", "-S", "-I", "-O", "-OO"}
    elif command in {"sh", "ash", "bash", "dash"}:
        expected = SHELL_SCRIPT
        options = {"-e", "-u", "-eu", "-ue", "-f"}
    else:
        return None
    for argument in remainder:
        if argument in options:
            continue
        return expected if argument == expected else None
    return None


def voice_identity(pid_path, proc_root="/proc"):
    """Return a verified live script PID/start-time pair, or None.

    No kill(pid, 0) is used: a PID file pointing at an unrelated or recycled
    process does not establish voice ownership. Exact script argv and the
    kernel process start-time are both part of this endpoint's identity.
    """
    try:
        raw_pid = Path(pid_path).read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[0-9]{1,10}", raw_pid):
            return None
        pid = int(raw_pid)
        if pid <= 1:
            return None
        process = Path(proc_root) / str(pid)
        first_stat = (process / "stat").read_text(encoding="ascii")
        tail = first_stat.rsplit(")", 1)[1].split()
        if tail[0] in {"Z", "X", "x"} or not tail[19].isdigit():
            return None
        with (process / "cmdline").open("rb") as source:
            raw = source.read(8193)
        if len(raw) > 8192:
            return None
        arguments = [token.decode("utf-8", errors="strict") for token in raw.split(b"\0") if token]
        script = _script_command(arguments)
        if script is None:
            return None
        # Detect disappearance/reuse or exec-state changes during inspection.
        second_tail = (process / "stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()
        if second_tail[0] in {"Z", "X", "x"} or second_tail[19] != tail[19]:
            return None
        return {"pid": pid, "start_time": tail[19], "script": script}
    except (OSError, UnicodeError, ValueError, IndexError):
        return None


class VolumeService(BaseVolumeService):
    def __init__(self, *args, log_path=None, pid_path=None, proc_root="/proc",
                 cache_seconds=0.5, **kwargs):
        super().__init__(*args, **kwargs)
        self.conversation_log_path = Path(log_path or VOICE_DIRECTORY + "/voice-assistant.log")
        self.voice_pid_path = Path(pid_path or VOICE_DIRECTORY + "/voice-assistant.pid")
        self.proc_root = Path(proc_root)
        self.cache_seconds = min(0.5, max(0.0, float(cache_seconds)))
        self._snapshot_lock = threading.Lock()
        self._cached_at = float("-inf")
        self._cached_identity = None
        self._cached_snapshot = None
        self._observed_identity = None
        self._session_confirmed = False

    def _log_has_startup(self):
        try:
            with self.conversation_log_path.open("rb") as source:
                header = source.read(4096).decode("utf-8", errors="replace")
            return any(LOG_START.match(line) for line in header.splitlines())
        except OSError:
            return False

    def conversation_snapshot(self):
        with self._snapshot_lock:
            identity = voice_identity(self.voice_pid_path, self.proc_root)
            key = (identity["pid"], identity["start_time"]) if identity else None
            now = time.monotonic()
            if (self._cached_snapshot is not None and self._cached_identity == key
                    and 0 <= now - self._cached_at < self.cache_seconds):
                return dict(self._cached_snapshot)
            if key != self._observed_identity:
                self._observed_identity = key
                self._session_confirmed = False
            result = read_snapshot(self.conversation_log_path, running=identity is not None,
                                   pid=identity["pid"] if identity else 0)
            after = voice_identity(self.voice_pid_path, self.proc_root)
            if after != identity:
                # Do not combine text read for one process with the next
                # process's PID. A later poll will observe the new session.
                identity, key = None, None
                self._observed_identity = None
                self._session_confirmed = False
                result = parse_log("", running=False, pid=0)
                result["log_available"] = False
                result["log_bytes"] = 0
            if identity:
                self._session_confirmed = (self._session_confirmed or result["session_started"]
                                           or self._log_has_startup())
                if not self._session_confirmed:
                    result.update(question="", answer="", turn_id="", answer_partial=False,
                                  phase="running", phase_label="语音运行中", phase_uncertain=True,
                                  feedback="等待当前语音进程写入启动日志。", warning="")
            result["start_time"] = identity["start_time"] if identity else ""
            result["process_verified"] = identity is not None
            result["session_confirmed"] = bool(identity and self._session_confirmed)
            self._cached_snapshot, self._cached_identity = result, key
            self._cached_at = now
            return dict(result)

    def start(self):
        try:
            super().start()
            existing_handler = self.server.RequestHandlerClass
            service = self

            class Handler(existing_handler):
                def do_GET(self):
                    if self.path != "/api/ui/conversation":
                        return super().do_GET()
                    try:
                        self.reply(200, service.conversation_snapshot())
                    except Exception:
                        # Keep errors local to this read-only view. Existing
                        # volume/control endpoints retain their base handler.
                        self.reply(503, {"error": "读取对话状态失败"})

            self.server.RequestHandlerClass = Handler
            return self
        except Exception as error:
            try:
                self.mixer.log("UI_CONVERSATION_START_FAILED: {}".format(error))
            except Exception:
                pass
            # BaseVolumeService.close owns its monitor, socket and thread.
            self.close()
            raise
