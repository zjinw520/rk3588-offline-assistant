"""One-shot NPU trial with verified old-backend stop and automatic restore.

Run on the board as root. No startup/config/model files are replaced.
Only the known RKLLama service tree receives SIGTERM, through pidfds.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import select
import signal
import socket
import subprocess
import sys
import time
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_ai.config import load_settings
from local_ai.guard import require_exclusive_runtime, runtime_users
from local_ai.runtime import verify_runtime

ENTRY = "/userdata/rkllama-venv/bin/rkllama_server"
PIDFILE = Path("/userdata/rkllama-lab/rkllama-server.pid")
OLD_LOG = Path("/userdata/rkllama-lab/rkllama-server.log")
OPENER = build_opener(ProxyHandler({}))


class TrialError(RuntimeError):
    pass


def say(text):
    print(text, flush=True)


def proc_info(pid, proc=Path("/proc")):
    folder = proc / str(pid)
    stat = (folder / "stat").read_text()
    fields = stat[stat.rfind(")") + 2:].split()
    args = [os.fsdecode(arg) for arg in (folder / "cmdline").read_bytes().split(b"\0") if arg]
    return {"pid": pid, "state": fields[0], "ppid": int(fields[1]),
            "start": fields[19], "argv": args}


def validate_old(info):
    argv = info["argv"]
    port = any(arg == "--port=8080" or (arg == "--port" and argv[i + 1:i + 2] == ["8080"])
               for i, arg in enumerate(argv))
    if info["pid"] <= 1 or info["state"] == "Z" or ENTRY not in argv or not port:
        raise TrialError("PID file does not identify the expected RKLLama 8080 service; nothing stopped")


class ProcessRef:
    def __init__(self, info):
        self.info = info
        self.fd = os.pidfd_open(info["pid"], 0)
        try:
            current = proc_info(info["pid"])
            if current["start"] != info["start"] or current["argv"] != info["argv"]:
                raise TrialError("Process identity changed; nothing signalled")
        except BaseException:
            os.close(self.fd)
            raise
    def alive(self):
        return not select.select([self.fd], [], [], 0)[0]
    def terminate(self):
        try:
            signal.pidfd_send_signal(self.fd, signal.SIGTERM, None, 0)
        except ProcessLookupError:
            pass
    def close(self):
        os.close(self.fd)


def descendants(root_pid, table):
    selected = {root_pid}
    while True:
        more = {pid for pid, info in table.items() if info["ppid"] in selected}
        if more <= selected:
            return selected
        selected |= more


def snapshot():
    table = {}
    for folder in Path("/proc").iterdir():
        if folder.name.isdigit():
            try:
                info = proc_info(int(folder.name))
                table[info["pid"]] = info
            except (FileNotFoundError, ProcessLookupError):
                pass
    return table


def json_get(url, timeout=5):
    with OPENER.open(url, timeout=timeout) as response:
        return json.load(response)


def wait_for(check, seconds, label):
    deadline = time.monotonic() + seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (OSError, ValueError) as error:
            last_error = str(error)
        time.sleep(.2)
    raise TrialError(label + " timed out" + (": " + last_error if last_error else ""))


def tags_ready(url):
    value = json_get(url, timeout=2)
    names = {item.get("name") for item in value.get("models", [])}
    return {"qwen2-0.5b", "deepseek-r1-1.5b"} <= names


def chat_once(base, model, timeout=600):
    question = "请只用一句简短的中文介绍你自己。"
    value = {"model": model, "messages": [{"role": "user", "content": question}], "stream": True}
    request = Request(base + "/api/chat", data=json.dumps(value, ensure_ascii=False).encode(),
                      headers={"Content-Type": "application/json"})
    started = time.monotonic()
    texts, first, final, total = [], None, None, 0
    deadline = started + timeout
    with OPENER.open(request, timeout=120) as response:
        while True:
            if time.monotonic() > deadline:
                raise TrialError("Chat exceeded its total time limit")
            line = response.readline(1024 * 1024 + 1)
            if not line:
                raise TrialError("Chat ended without done=true")
            if len(line) > 1024 * 1024:
                raise TrialError("Chat line too large")
            chunk = json.loads(line)
            if not isinstance(chunk, dict):
                raise TrialError("Invalid chat response")
            if chunk.get("error"):
                raise TrialError("Model failed: " + str(chunk["error"]))
            if chunk.get("model") != model:
                raise TrialError("Response model differs from the request")
            message = chunk.get("message")
            text = message.get("content") if isinstance(message, dict) else None
            if not isinstance(text, str):
                raise TrialError("Invalid content field")
            total += len(text.encode())
            if total > 1024 * 1024:
                raise TrialError("Chat output too large")
            if text:
                first = first if first is not None else time.monotonic() - started
                texts.append(text)
            if chunk.get("done") is True:
                final = chunk
                break
    answer = "".join(texts).strip()
    if not answer or not final:
        raise TrialError("Model returned no text")
    status = json_get(base + "/healthz")
    if not status.get("hardware_inference_verified") or status.get("loaded_model") != model:
        raise TrialError("Server did not confirm successful model execution")
    result = {"model": model, "question": question, "text": answer,
              "first_chunk_seconds": round(first, 3), "seconds": round(time.monotonic() - started, 3),
              "eval_count": final.get("eval_count"), "done_reason": final.get("done_reason")}
    say("TRIAL_CHAT_OK " + json.dumps(result, ensure_ascii=False))
    return result


class BoardTrial:
    def __init__(self, root=ROOT):
        self.root = root
        self.refs = []
        self.master = None
        self.candidate = None
        self.old_env = None
        self.old_cwd = None
        self.original_pid_text = None
        self.touched = False
        self.results = []
        self.old_log = None
        self.new_log = None
        self.board_info = {}

    def preflight(self):
        if platform.system() != "Linux" or platform.machine() != "aarch64" or os.geteuid() != 0:
            raise TrialError("Run this trial on the RK3588 board as root")
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise TrialError("Python lacks pidfd support; old service was not stopped")
        settings = load_settings(self.root / "config.local.json")
        fingerprint = verify_runtime(settings.runtime)
        driver = Path("/sys/kernel/debug/rknpu/version")
        self.board_info = {"kernel": platform.release(), "libc": platform.libc_ver(),
                           "rknpu": driver.read_text().strip() if driver.exists() else "unavailable",
                           "runtime_sha256": fingerprint["sha256"]}
        voice = json_get("http://127.0.0.1:8081/api/voice/status")
        if voice.get("running") is not False:
            raise TrialError("Voice conversation is running; exit the assistant app before the trial")
        if not tags_ready("http://127.0.0.1:8080/api/tags"):
            raise TrialError("The old backend is not healthy; repair it before this trial")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 18080))
        self.original_pid_text = PIDFILE.read_text()
        text = self.original_pid_text.strip()
        if not text.isdigit():
            raise TrialError("Invalid old PID file")
        info = proc_info(int(text))
        validate_old(info)
        self.master = ProcessRef(info)
        self.refs.append(self.master)
        table = snapshot()
        if info["pid"] not in table or table[info["pid"]]["start"] != info["start"]:
            raise TrialError("Old process changed during inspection")
        for pid in sorted(descendants(info["pid"], table) - {info["pid"]}):
            try:
                self.refs.append(ProcessRef(table[pid]))
            except (FileNotFoundError, ProcessLookupError):
                pass
        unknown = set(runtime_users()) - {ref.info["pid"] for ref in self.refs}
        if unknown:
            raise TrialError("Other model runtime processes found; nothing stopped: " + str(sorted(unknown)))
        folder = Path("/proc") / str(info["pid"])
        self.old_cwd = os.readlink(folder / "cwd")
        self.old_env = dict(os.fsdecode(pair).split("=", 1) for pair in (folder / "environ").read_bytes().split(b"\0") if b"=" in pair)
        # Open outputs before touching the working service, so permission
        # failures cannot strand it. The old log is appended, never truncated.
        self.old_log = OLD_LOG.open("ab", buffering=0)
        self.new_log = (self.root / "trial-server.log").open("ab", buffering=0)
        self.new_log.write(b"\n=== candidate NPU trial ===\n")
        say("TRIAL_PREFLIGHT_OK old_pid={} tree={}".format(info["pid"], [r.info["pid"] for r in self.refs]))

    def stop_old(self):
        if PIDFILE.read_text() != self.original_pid_text:
            raise TrialError("Old PID file changed; nothing stopped")
        self.touched = True
        for ref in self.refs:
            ref.terminate()
        wait_for(lambda: all(not ref.alive() for ref in self.refs), 15, "Old backend stop")
        require_exclusive_runtime()
        say("OLD_BACKEND_STOPPED (startup and web service unchanged)")

    def run_candidate(self):
        env = os.environ.copy()
        env["LOCAL_AI_PYTHON"] = "/userdata/rkllama-venv/bin/python"
        self.candidate = subprocess.Popen(["/bin/sh", str(self.root / "run_candidate.sh"),
                                           "--port", "18080", "--enable-inference"],
                                          cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                                          stdout=self.new_log, stderr=subprocess.STDOUT, start_new_session=True)
        def ready():
            if self.candidate.poll() is not None:
                raise TrialError("Candidate stopped; inspect trial-server.log")
            value = json_get("http://127.0.0.1:18080/healthz", timeout=2)
            return value.get("backend") == "local-ai-independent" and value.get("inference_enabled") is True
        wait_for(ready, 20, "Candidate startup")
        base = "http://127.0.0.1:18080"
        for name in ("qwen2-0.5b", "deepseek-r1-1.5b", "qwen2-0.5b"):
            say("TRIAL_CHAT_START " + name + " (loading/generating; please wait)")
            self.results.append(chat_once(base, name))
        request = Request(base + "/api/unload", data=b"{}", headers={"Content-Type": "application/json"})
        with OPENER.open(request, timeout=30) as response:
            if json.load(response).get("unloaded") is not True:
                raise TrialError("Candidate model unload failed")
        if json_get(base + "/healthz").get("loaded_model"):
            raise TrialError("Candidate model is still loaded")
        say("TRIAL_UNLOAD_OK")

    def stop_candidate(self):
        if self.candidate is not None and self.candidate.poll() is None:
            self.candidate.terminate()
            try:
                self.candidate.wait(timeout=20)
            except subprocess.TimeoutExpired:
                # This is exclusively our newly spawned experimental process.
                self.candidate.kill()
                self.candidate.wait(timeout=10)
        if self.candidate is not None:
            say("CANDIDATE_STOPPED exit=" + str(self.candidate.returncode))

    def restore_old(self):
        if not self.touched:
            return
        if any(ref.alive() for ref in self.refs):
            # An interruption may have happened during the stop loop. Finish
            # stopping only those already captured identities before restart.
            for ref in self.refs:
                ref.terminate()
            wait_for(lambda: all(not ref.alive() for ref in self.refs), 15, "Old process cleanup")
        require_exclusive_runtime()
        if PIDFILE.exists() and PIDFILE.read_text() != self.original_pid_text:
            raise TrialError("PID file changed externally; no duplicate backend started")
        process = subprocess.Popen(self.master.info["argv"], cwd=self.old_cwd, env=self.old_env,
                                   stdin=subprocess.DEVNULL, stdout=self.old_log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        # write_text only targets the known service PID file; do not alter
        # startup scripts, models, runtime libraries or the web PID file.
        PIDFILE.write_text(str(process.pid) + "\n")
        def ready():
            if process.poll() is not None:
                raise TrialError("Old backend exited during restore; inspect its server log")
            return tags_ready("http://127.0.0.1:8080/api/tags")
        wait_for(ready, 30, "Old backend restore")
        say("OLD_BACKEND_RESTORED pid=" + str(process.pid) + " port=8080")

    def close(self):
        for ref in self.refs:
            ref.close()
        for log in (self.old_log, self.new_log):
            if log is not None:
                log.close()


def execute(trial):
    error, restore_error = None, None
    try:
        trial.preflight()
        trial.stop_old()
        trial.run_candidate()
    except BaseException as failure:
        error = str(failure) or type(failure).__name__
        say("TRIAL_FAILED " + error)
    finally:
        # Further terminal signals should not interrupt recovery halfway.
        handlers = {}
        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            sig = getattr(signal, name, None)
            if sig is not None:
                handlers[sig] = signal.signal(sig, signal.SIG_IGN)
        try:
            trial.stop_candidate()
        except BaseException as failure:
            restore_error = "Candidate stop: " + str(failure)
        try:
            trial.restore_old()
        except BaseException as failure:
            restore_error = (restore_error + "; " if restore_error else "") + str(failure)
        finally:
            trial.close()
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    report = {"ok": error is None and restore_error is None, "error": error,
              "restore_error": restore_error, "results": trial.results,
              "board": getattr(trial, "board_info", {})}
    (trial.root / "trial-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if restore_error:
        say("OLD_BACKEND_RESTORE_FAILED " + restore_error)
        say("Inspect logs. Startup configuration was not changed; a normal reboot returns to the existing deployment.")
    say("NPU_TRIAL_OK_OLD_RESTORED" if report["ok"] else "NPU_TRIAL_FAILED")
    return 0 if report["ok"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    if platform.system() != "Linux":
        raise SystemExit("This tool only runs on the Linux board")
    import fcntl
    with (ROOT / "trial.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another trial is already running")
        def interrupted(signum, frame):
            raise TrialError("Trial interrupted by signal " + str(signum))
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, interrupted)
        return execute(BoardTrial())


if __name__ == "__main__":
    raise SystemExit(main())
