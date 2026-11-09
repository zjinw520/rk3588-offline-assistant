"""Back up and patch only the documented ATK SystemUI startup command."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

BASE = Path("/userdata/voice-assistant")
TARGET = Path("/etc/init.d/S50systemui")
LIBRARY = BASE / "libsystemuiwake.so"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def patched(text, library):
    library = str(library)
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", library):
        raise ValueError("Unsupported preload library path")
    if "RK3588_SYSTEMUI_WAKE_BRIDGE" in text:
        raise ValueError("Startup is already patched; inspect the existing backup")
    lines = text.splitlines(keepends=True)
    matches = [(i, re.fullmatch(r"([ \t]*)/opt/ui/systemui([ \t]*>[^\r\n]*|[ \t]*&|[ \t]*)\r?\n?", line))
               for i, line in enumerate(lines)]
    matches = [(i, match) for i, match in matches if match]
    if len(matches) != 1:
        raise ValueError("Expected exactly one direct SystemUI launch line; no file changed")
    index, match = matches[0]
    newline = "\r\n" if lines[index].endswith("\r\n") else "\n" if lines[index].endswith("\n") else ""
    lines[index] = (match[1] + 'env RK3588_SYSTEMUI_WAKE_BRIDGE=1 LD_PRELOAD="'
                    + library + '${LD_PRELOAD:+:$LD_PRELOAD}" /opt/ui/systemui'
                    + match[2] + newline)
    return "".join(lines)


def install():
    if TARGET.is_symlink() or not TARGET.is_file():
        raise ValueError("Expected a regular ATK S50systemui startup file")
    for path in (LIBRARY, BASE / "systemui-wake-selftest"):
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing tested bridge artifact: " + str(path))
        result = subprocess.run(["ldd", str(path)], capture_output=True, text=True, timeout=15)
        if result.returncode or "not found" in (result.stdout + result.stderr):
            raise ValueError("Unresolved bridge dependencies: " + str(path))
    subprocess.run([str(BASE / "systemui-wake-selftest")], check=True, timeout=30)
    original = TARGET.read_bytes()
    changed = patched(original.decode("utf-8"), LIBRARY).encode("utf-8")
    backups = BASE / "backups"
    backups.mkdir(exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="factory-startup-", dir=backups))
    shutil.copy2(TARGET, backup / "S50systemui")
    candidate = backup / "S50systemui.patched"
    candidate.write_bytes(changed)
    subprocess.run(["/bin/sh", "-n", str(candidate)], check=True)
    record = {"original": digest(backup / "S50systemui"), "installed": digest(candidate)}
    (backup / "manifest.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    if TARGET.read_bytes() != original:
        raise ValueError("Startup changed during preparation; installation stopped")
    try:
        TARGET.write_bytes(changed)
        subprocess.run(["/bin/sh", "-n", str(TARGET)], check=True)
    except BaseException:
        shutil.copy2(backup / "S50systemui", TARGET)
        raise
    print("FACTORY_STARTUP_PATCHED_REBOOT_REQUIRED")
    print("Backup:", backup)
    print("Restore: python3 tools/patch_factory_startup.py --rollback " + str(backup))


def rollback(backup):
    backup = Path(backup).resolve()
    if backup.parent != (BASE / "backups").resolve() or not backup.name.startswith("factory-startup-"):
        raise ValueError("Unexpected backup directory")
    if TARGET.is_symlink() or (backup / "S50systemui").is_symlink():
        raise ValueError("Refusing startup symlink")
    record = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    if digest(backup / "S50systemui") != record["original"]:
        raise ValueError("Backup checksum differs")
    current = digest(TARGET)
    if current == record["original"]:
        print("FACTORY_STARTUP_ALREADY_RESTORED")
        return
    if current != record["installed"]:
        raise ValueError("Startup was modified after installation; refusing to overwrite it")
    shutil.copy2(backup / "S50systemui", TARGET)
    print("FACTORY_STARTUP_RESTORED_REBOOT_REQUIRED")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    if sys.platform != "linux" or os.geteuid() != 0:
        parser.error("Run as root on the ATK board")
    import fcntl
    with Path("/tmp/rk3588-factory-startup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.rollback:
            rollback(args.rollback)
        else:
            install()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("FACTORY_STARTUP_ERROR " + str(error), file=sys.stderr)
        raise SystemExit(1)
