#!/usr/bin/env python3
"""Install one native assistant entry and archive the old browser launchers."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
BASE = Path("/userdata/voice-assistant")
BACKUPS = BASE / "backups"
APPS = Path("/opt/ui/src/apps")
CFG_DIR = Path("/opt/ui/src/ATK-DLRK3588")
ENTRY = APPS / "rk3588-ai"
ICON = Path("/opt/ui/src/appicons/rk3588-ai.png")
WESTON = Path("/etc/xdg/weston")
OLD_ENTRY = str(BASE / "open_offline_assistant.sh")
APP_LINE = "appicons/rk3588-ai.png 离线AI助手 rk3588-ai"
OLD_PATHS = (
    BASE / "open_offline_assistant.sh",
    BASE / "rk3588-ai-systemui",
    BASE / "rk3588-offline-assistant.desktop",
    BASE / "rk3588-ai-assistant.svg",
    BASE / "chromium-profile",
    Path("/root/Desktop/rk3588-offline-assistant.desktop"),
    Path("/usr/share/applications/rk3588-offline-assistant.desktop"),
    WESTON / "weston.ini.d/99-rk3588-ai.ini",
)


def read_config(path):
    with Path(path).open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


def stored_path(prefix, target):
    target = Path(target)
    return (Path(prefix) / target.relative_to(target.anchor)).as_posix()


def app_list(text, include):
    """Preserve other apps, retaining exactly one assistant on page one."""
    lines = text.splitlines(keepends=True)
    output = []
    found = False
    for line in lines:
        fields = line.split()
        own = not line.lstrip().startswith("#") and len(fields) >= 3 and fields[-1] == "rk3588-ai"
        if own:
            if include and not found:
                output.append(APP_LINE + "\n")
                found = True
        else:
            output.append(line)
    if include and not found:
        if output and not output[-1].endswith(("\n", "\r")):
            output[-1] += "\n"
        output.append(APP_LINE + "\n")
    return "".join(output)


def remove_old_weston_entries(text):
    sections = []
    block = []
    for line in text.splitlines(keepends=True):
        if re.match(r"^\s*\[[^\]]+\]", line) and block:
            sections.append(block)
            block = []
        block.append(line)
    if block:
        sections.append(block)
    output = []
    for block in sections:
        header = block[0].strip()
        owned = header in ("[launcher]", "[desktop-launcher]") and any(
            line.strip() == "path=" + OLD_ENTRY for line in block)
        if not owned:
            output.extend(block)
    return "".join(output)


def allowed_target(path):
    path = Path(path)
    fixed = {ENTRY, ICON, *OLD_PATHS, WESTON / "weston.ini"}
    if path in fixed:
        return True
    if path.parent == CFG_DIR and path.name in ("apk1.cfg", "apk2.cfg", "apk3.cfg"):
        return True
    return path.parent == WESTON / "weston.ini.d" and path.suffix == ".ini"


def existing_old_browser():
    expected = ("--user-data-dir=" + str(BASE / "chromium-profile")).encode()
    pids = []
    for directory in Path("/proc").glob("[0-9]*"):
        try:
            args = (directory / "cmdline").read_bytes().split(b"\0")
            if args and b"chromium" in args[0] and expected in args:
                pids.append(int(directory.name))
        except (OSError, ValueError):
            pass
    return pids


class Installation:
    def __init__(self, backup):
        self.backup = Path(backup)
        self.record = {"version": 1, "state": "installing", "changed": [], "archived": []}

    def save(self):
        (self.backup / "install.json").write_text(
            json.dumps(self.record, ensure_ascii=False, indent=2), encoding="utf-8")

    def replace(self, target, content, executable=False):
        target = Path(target)
        if not allowed_target(target) or target.is_symlink():
            raise RuntimeError("Refusing unexpected/symlink target: {}".format(target))
        present = target.exists()
        if present and target.read_bytes() == content:
            if executable:
                target.chmod(0o755)
            return
        saved = None
        if present:
            saved = stored_path("original", target)
            destination = self.backup / saved
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(target), str(destination))
        self.record["changed"].append({"target": str(target), "existed": present, "saved": saved})
        self.save()
        target.write_bytes(content)
        if executable:
            target.chmod(0o755)
        print("Installed: {}".format(target), flush=True)

    def archive(self, target):
        target = Path(target)
        if not target.exists() and not target.is_symlink():
            return
        if target not in OLD_PATHS or target.is_symlink():
            raise RuntimeError("Refusing unexpected/symlink archive target: {}".format(target))
        saved = stored_path("retired", target)
        destination = self.backup / saved
        destination.parent.mkdir(parents=True, exist_ok=True)
        item = {"target": str(target), "saved": saved, "moved": False}
        self.record["archived"].append(item)
        self.save()
        # These explicit old UI paths are moved into the same userdata volume.
        # No model, backend service, or system Chromium installation is removed.
        shutil.move(str(target), str(destination))
        item["moved"] = True
        self.save()
        print("Archived old UI: {}".format(target), flush=True)


def rollback(backup):
    backup = Path(backup).resolve()
    if backup.parent != BACKUPS.resolve() or not backup.name.startswith("systemui-"):
        raise RuntimeError("Backup must be a systemui-* directory inside {}".format(BACKUPS))
    record = json.loads((backup / "install.json").read_text(encoding="utf-8"))

    def saved_file(relative):
        path = (backup / relative).resolve()
        if backup not in path.parents:
            raise RuntimeError("Invalid backup path")
        return path

    for item in reversed(record["archived"]):
        target = Path(item["target"])
        if target not in OLD_PATHS or target.is_symlink():
            raise RuntimeError("Unexpected restore target: {}".format(target))
        saved = saved_file(item["saved"])
        if saved.exists():
            if target.exists():
                if not item.get("moved", True):
                    continue  # A failed cross-filesystem move left its source intact.
                raise RuntimeError("Keep both copies; restore target already exists: {}".format(target))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(saved), str(target))
    for item in reversed(record["changed"]):
        target = Path(item["target"])
        if not allowed_target(target) or target.is_symlink():
            raise RuntimeError("Unexpected restore target: {}".format(target))
        if item["existed"]:
            shutil.copy2(str(saved_file(item["saved"])), str(target))
        elif target.is_file():
            target.unlink()
    record["state"] = "rolled-back"
    (backup / "install.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Restored SystemUI entry from {}. Reboot to reload its app list.".format(backup), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollback", type=Path, help="restore an exact printed backup directory")
    args = parser.parse_args()
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("Run this installer as root on the RK3588 board")
    import fcntl

    with Path("/tmp/rk3588-assistant-qml.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Exit the native assistant window before installing/restoring")
        if args.rollback:
            rollback(args.rollback)
            return 0
        required = ("systemui_entry.sh", "rk3588-ai.png", "run_qml_assistant.sh", "launch.py", "rk3588-ai")
        if any(not (HERE / name).is_file() for name in required):
            raise RuntimeError("Installation package is incomplete")
        if not (CFG_DIR / "apk1.cfg").is_file() or not APPS.is_dir() or not ICON.parent.is_dir():
            raise RuntimeError("Expected ATK-DLRK3588 SystemUI directories were not found")
        if (HERE / "rk3588-ai.png").read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("Native app icon is not a PNG")
        # Both services are existing prerequisites, not new daemons to install.
        subprocess.run(["/bin/sh", str(HERE / "run_qml_assistant.sh"),
                        "--check", "--from-systemui"], check=True)
        if existing_old_browser():
            raise RuntimeError("The old assistant Chromium is still running; close it before installation")

        BACKUPS.mkdir(parents=True, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix="systemui-", dir=str(BACKUPS)))
        install = Installation(backup)
        install.save()
        print("Backup: {}".format(backup), flush=True)
        try:
            install.replace(ENTRY, (HERE / "systemui_entry.sh").read_bytes(), executable=True)
            install.replace(ICON, (HERE / "rk3588-ai.png").read_bytes())
            for page in ("apk1.cfg", "apk2.cfg", "apk3.cfg"):
                path = CFG_DIR / page
                if path.is_file():
                    original = read_config(path)
                    changed = app_list(original, include=(page == "apk1.cfg"))
                    install.replace(path, changed.encode("utf-8"))
            configs = [WESTON / "weston.ini"] + sorted((WESTON / "weston.ini.d").glob("*.ini"))
            for path in configs:
                if path.is_file() and path not in OLD_PATHS:
                    original = read_config(path)
                    changed = remove_old_weston_entries(original)
                    install.replace(path, changed.encode("utf-8"))
            for path in OLD_PATHS:
                install.archive(path)
            install.record["state"] = "installed"
            install.save()
        except Exception:
            print("Installation failed; restoring recorded changes.", flush=True)
            rollback(backup)
            raise
        print("\nNative assistant installed once on SystemUI page one.", flush=True)
        print("Old browser launchers/profile were archived; models and 8080/8081 services remain.", flush=True)
        print("Run sync, then reboot to reload SystemUI without spawning another sysvolume.", flush=True)
        print("Restore command: python3 {} --rollback {}".format(HERE / "install_systemui.py", backup), flush=True)
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("Install failed: {}".format(error), file=sys.stderr)
        raise SystemExit(1)
