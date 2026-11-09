"""Back up and patch only the model-service parts of the existing SysV entry."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from trial_board import BoardTrial, TrialError, json_get, tags_ready, wait_for

STARTUP = Path("/etc/init.d/S99rkllama")
MARKER = "# LOCAL_AI_BACKEND_DELEGATE_V1"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def patch_startup(text, root):
    if MARKER in text:
        raise TrialError("Independent backend startup is already installed")
    if 'VENV="/userdata/rkllama-venv"' not in text or 'RK3588_SERVICE_PID_IDENTITY_V1' not in text:
        raise TrialError("Unrecognized startup layout; nothing changed")
    manager = str(root / "tools/service_control.py")
    if any(c in manager for c in ('"', '$', '`', '\n', '\r')):
        raise TrialError("Unsupported application path")
    call = '"$VENV/bin/python" "' + manager + '"'
    for function, key, command in (("is_running", '"$1"', "status --quiet"),
                                    ("stop_one", '"$2"', "stop")):
        anchor = function + "() {\n"
        if text.count(anchor) != 1:
            raise TrialError("Unrecognized startup function: " + function)
        inserted = (anchor + "    " + MARKER + "\n    if [ " + key + ' = "$RK_PID" ]; then\n'
                    + "        " + call + " " + command + "\n        return $?\n    fi\n")
        text = text.replace(anchor, inserted, 1)
    pattern = re.compile(r'^    if is_running "\$RK_PID"; then\n.*?^    fi\n', re.M | re.S)
    matches = list(pattern.finditer(text))
    # There is one startup block and one status block. Only the startup block
    # contains the original executable and fixed port; never replace by order.
    starts = [match for match in matches if '"$VENV/bin/rkllama_server"' in match.group() and '--port 8080' in match.group()]
    if len(starts) != 1:
        raise TrialError("Cannot isolate the original model start block")
    match = starts[0]
    text = text[:match.start()] + "    " + call + " start || return 1\n" + text[match.end():]
    text = text.replace('echo "RKLLama API:', 'echo "Independent AI API:')
    text = text.replace('echo "RKLLama: ', 'echo "Independent AI: ')
    return text


def atomic_write(path, data, mode):
    temporary = path.with_name(path.name + ".local-ai-" + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as output:
            os.chmod(temporary, mode)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def service(command):
    subprocess.run(["/userdata/rkllama-venv/bin/python", str(ROOT / "tools/service_control.py"), command], check=True)


def install():
    trial = BoardTrial(ROOT)
    changed = False
    original = STARTUP.read_bytes()
    patched = patch_startup(original.decode(), ROOT).encode()
    mode = stat.S_IMODE(STARTUP.stat().st_mode)
    backup = ROOT / "backups" / ("backend-before-v1-" + uuid.uuid4().hex[:12])
    backup.mkdir(parents=True, mode=0o700)
    atomic_write(backup / "S99rkllama.original", original, mode)
    atomic_write(backup / "S99rkllama.independent", patched, mode)
    metadata = {"format": 1, "root": str(ROOT), "original_sha256": sha(original), "patched_sha256": sha(patched), "mode": mode}
    atomic_write(backup / "backup.json", json.dumps(metadata).encode(), 0o600)
    print("BACKEND_BACKUP " + str(backup), flush=True)
    subprocess.run(["/bin/sh", "-n", str(backup / "S99rkllama.independent")], check=True)
    try:
        trial.preflight()
        if STARTUP.read_bytes() != original:
            raise TrialError("Startup changed externally; nothing stopped")
        trial.stop_old()
        atomic_write(STARTUP, patched, mode)
        changed = True
        service("start")
        if not tags_ready("http://127.0.0.1:8080/api/tags") or json_get("http://127.0.0.1:8080/healthz").get("backend") != "local-ai-independent":
            raise TrialError("New backend did not become ready")
        subprocess.run(["/bin/sh", str(STARTUP), "status"], check=True)
        atomic_write(ROOT / "active-backend-backup.txt", (str(backup) + "\n").encode(), 0o600)
        print("INDEPENDENT_BACKEND_INSTALLED port=8080", flush=True)
        print("Rollback: python3 " + str(ROOT / "tools/install_backend.py") + " --rollback " + str(backup), flush=True)
    except BaseException:
        if changed:
            try:
                service("stop")
            finally:
                atomic_write(STARTUP, original, mode)
        trial.restore_old()
        print("BACKEND_INSTALL_ROLLED_BACK", flush=True)
        raise
    finally:
        trial.close()


def rollback(folder):
    folder = folder.resolve(strict=True)
    if not folder.is_relative_to((ROOT / "backups").resolve()):
        raise TrialError("Backup must be inside this application's backups folder")
    metadata = json.loads((folder / "backup.json").read_text())
    original = (folder / "S99rkllama.original").read_bytes()
    if metadata.get("root") != str(ROOT) or sha(original) != metadata["original_sha256"]:
        raise TrialError("Backup identity or checksum differs")
    current = STARTUP.read_bytes()
    if sha(current) == metadata["original_sha256"]:
        subprocess.run(["/bin/sh", str(STARTUP), "start"], check=True)
        wait_for(lambda: tags_ready("http://127.0.0.1:8080/api/tags"), 30, "Original backend restore")
        print("OLD_STARTUP_ALREADY_RESTORED")
        return
    if sha(current) != metadata["patched_sha256"]:
        raise TrialError("Startup was edited after installation; refusing to overwrite it")
    voice = json_get("http://127.0.0.1:8081/api/voice/status")
    if voice.get("running") is not False:
        raise TrialError("Stop the voice conversation before rollback")
    service("stop")
    atomic_write(STARTUP, original, metadata["mode"])
    subprocess.run(["/bin/sh", str(STARTUP), "start"], check=True)
    wait_for(lambda: tags_ready("http://127.0.0.1:8080/api/tags"), 30, "Original backend restore")
    print("ORIGINAL_BACKEND_RESTORED", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    if os.name != "posix" or os.geteuid() != 0:
        raise SystemExit("Run on the Linux board as root")
    import fcntl
    with (ROOT / "trial.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.rollback:
            rollback(args.rollback)
        else:
            install()


if __name__ == "__main__":
    main()
