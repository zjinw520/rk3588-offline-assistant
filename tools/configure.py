"""Configure a standalone model service without an existing RKLLama deployment."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_ai.config import load_settings
from local_ai.elf import inspect_elf
from local_ai.runtime import verify_runtime
from tools.prepare_board import copy_vendor


def configure(root, runtime, models, gomp=None, context=1024, maximum=256, temperature=0.7):
    root = Path(root).resolve()
    config = root / "config.local.json"
    temporary = root / "config.preparing.json"
    if config.exists() or config.is_symlink() or temporary.exists() or temporary.is_symlink():
        raise ValueError("Configuration already exists; use a fresh directory or inspect it first")
    runtime = Path(runtime).resolve(strict=True)
    info = verify_runtime(runtime)
    bridge = root / "build/liblocalai-rkllm130.so"
    elf = inspect_elf(bridge)
    if elf["machine"] != 183:
        raise ValueError("Bridge must be Linux AArch64; use the aarch64 release asset or build it")
    records = [{"name": name, "path": str(Path(path).resolve(strict=True)),
                "tokenizer": str(Path(tokenizer).resolve(strict=True)),
                "context": context, "max_new_tokens": maximum, "temperature": temperature}
               for name, path, tokenizer in models]
    payload = {"format": 1, "runtime": "vendor-local/librkllmrt.so",
               "bridge": "build/liblocalai-rkllm130.so", "models": records}
    created = False
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            created = True
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        load_settings(temporary)
        vendor = root / "vendor-local"
        if vendor.is_symlink():
            raise ValueError("Refusing vendor-local symlink")
        vendor.mkdir(exist_ok=True)
        copy_vendor(runtime, vendor / "librkllmrt.so")
        candidate = Path(gomp) if gomp else runtime.parent / "libgomp.so.1"
        if gomp or candidate.is_file():
            copy_vendor(candidate.resolve(strict=True), vendor / "libgomp.so.1")
        # Exclusive creation also protects a configuration created since the first check.
        with config.open("x", encoding="utf-8") as stream:
            stream.write(temporary.read_text(encoding="utf-8"))
    finally:
        if created:
            temporary.unlink()
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--libgomp", type=Path, help="Optional board-compatible libgomp.so.1")
    parser.add_argument("--model", required=True, action="append", nargs=3,
                        metavar=("NAME", "RKLLM_FILE", "TOKENIZER_DIR"))
    parser.add_argument("--context", type=int, default=1024)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.7)
    args = parser.parse_args()
    try:
        info = configure(ROOT, args.runtime, args.model, args.libgomp, args.context,
                         args.max_new_tokens, args.temperature)
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, "CONFIG_FAILED: " + str(error) + "\n")
    print("STANDALONE_CONFIG_OK runtime=1.3.0 sha256=" + info["sha256"])
    print("Next: run tools/doctor.py with your board's Python environment.")
    print("No service, startup script, model or tokenizer was changed.")


if __name__ == "__main__":
    main()
