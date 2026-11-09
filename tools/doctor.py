"""Read-only board preflight. Does not initialize RKLLM or run an NPU model."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_ai.config import load_settings
from local_ai.elf import inspect_elf
from local_ai.guard import runtime_users
from local_ai.prompts import LocalPrompts
from local_ai.runtime import verify_runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.local.json")
    args = parser.parse_args()
    result = {"kernel": platform.release(), "machine": platform.machine(),
              "libc": platform.libc_ver(), "python": platform.python_version(),
              "npu_initialized": False, "models": []}
    try:
        if platform.system() != "Linux" or platform.machine() != "aarch64":
            raise RuntimeError("Run this preflight on the RK3588 board")
        settings = load_settings(args.config)
        result["runtime"] = verify_runtime(settings.runtime)
        result["bridge"] = inspect_elf(settings.bridge)
        if result["bridge"]["machine"] != 183:
            raise RuntimeError("Bridge is not AArch64")
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = str(settings.runtime.parent) + os.pathsep + env.get("LD_LIBRARY_PATH", "")
        ldd = subprocess.run(["ldd", str(settings.runtime)], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=15)
        result["runtime_ldd"] = ldd.stdout.strip()
        if ldd.returncode or "not found" in ldd.stdout:
            raise RuntimeError("Runtime dependencies are unresolved; see runtime_ldd")
        # Loading our tiny bridge alone does not load the official runtime.
        bridge = ctypes.CDLL(str(settings.bridge))
        bridge.la_abi.restype = ctypes.c_int32
        if bridge.la_abi() != 1:
            raise RuntimeError("Unexpected bridge ABI")
        prompts = LocalPrompts()
        import transformers
        result["transformers"] = transformers.__version__
        for model in settings.models.values():
            prompt = prompts.prepare(model, [{"role": "user", "content": "你好"}])
            result["models"].append({"name": model.name, "bytes": model.path.stat().st_size,
                                     "test_prompt_tokens": len(prompt.tokens),
                                     "context": model.context, "max_new_tokens": model.max_new_tokens})
        version = Path("/sys/kernel/debug/rknpu/version")
        result["rknpu_driver"] = version.read_text().strip() if version.is_file() else "unavailable"
        result["other_runtime_pids"] = runtime_users()
        result["ok"] = True
    except Exception as error:
        result["ok"] = False
        result["error"] = str(error)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("BOARD_PREFLIGHT_OK" if result["ok"] else "BOARD_PREFLIGHT_FAILED")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
