"""Refuse NPU use while another process has an RKLLM runtime mapped."""
import os
from pathlib import Path


def runtime_users(proc=Path("/proc")):
    users = []
    unreadable = []
    for item in proc.iterdir():
        if not item.name.isdigit() or int(item.name) == os.getpid():
            continue
        try:
            maps = (item / "maps").read_text(errors="replace")
        except FileNotFoundError:
            continue
        except PermissionError:
            unreadable.append(int(item.name))
            continue
        if "librkllmrt.so" in maps:
            users.append(int(item.name))
    if unreadable:
        raise RuntimeError("Cannot inspect all process maps; run the board test as root")
    return sorted(users)


def require_exclusive_runtime():
    users = runtime_users()
    if users:
        raise RuntimeError("Another RKLLM runtime is still mapped by PID(s): "
                           + ", ".join(map(str, users)) + ". Stop the old model backend before inference.")
