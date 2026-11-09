"""Create candidate-only config and copies of the existing vendor runtime."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_ai.config import load_settings
from local_ai.runtime import verify_runtime


def parse_modelfile(path):
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, text = line.partition("=")
        if not separator or not key.strip().replace("_", "").isalnum():
            raise ValueError("Unsupported Modelfile line in " + str(path))
        parts = shlex.split(text, comments=True, posix=True)
        if len(parts) != 1:
            raise ValueError("Expected one value for " + key)
        values[key.strip()] = parts[0]
    return values


def model_record(folder):
    values = parse_modelfile(folder / "Modelfile")
    path = Path(values["FROM"])
    if not path.is_absolute():
        path = folder / path
    candidates = [folder / "tokenizer"]
    local = values.get("HUGGINGFACE_PATH", "")
    if local:
        candidate = Path(local)
        candidates.append(candidate if candidate.is_absolute() else folder / candidate)
    candidates += [folder / "qwen2-tokenizer-modelscope"]
    tokenizer = next((p for p in candidates if (p / "tokenizer_config.json").is_file()), None)
    if tokenizer is None:
        raise ValueError("No local tokenizer found for " + folder.name)
    record = {"name": folder.name, "path": str(path.resolve()), "tokenizer": str(tokenizer.resolve()),
              "context": int(values.get("NUM_CTX", "1024")),
              "max_new_tokens": int(values.get("MAX_NEW_TOKENS", "256")),
              "temperature": float(values.get("TEMPERATURE", "0.7"))}
    if "SYSTEM" in values:
        record["system"] = values["SYSTEM"]
    return record


def copy_vendor(source, destination):
    if destination.is_symlink():
        raise ValueError("Refusing vendor symlink: " + str(destination))
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).digest() != hashlib.sha256(source.read_bytes()).digest():
            raise ValueError("Candidate vendor copy differs; use a fresh candidate directory")
    else:
        shutil.copyfile(source, destination)
        destination.chmod(0o644)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--models-root", type=Path, default=Path("/userdata/rkllama-models"))
    args = parser.parse_args()
    config = ROOT / "config.local.json"
    if config.exists():
        raise SystemExit("Candidate config already exists; it was not overwritten")
    runtime = args.runtime.resolve(strict=True)
    info = verify_runtime(runtime)
    records = [model_record((args.models_root / name).resolve())
               for name in ("qwen2-0.5b", "deepseek-r1-1.5b")]
    vendor = ROOT / "vendor-local"
    if vendor.is_symlink():
        raise SystemExit("Refusing vendor-local symlink")
    vendor.mkdir(exist_ok=True)
    copy_vendor(runtime, vendor / "librkllmrt.so")
    gomp = runtime.parent / "libgomp.so.1"
    if gomp.is_file():
        copy_vendor(gomp.resolve(), vendor / "libgomp.so.1")
    payload = {"format": 1, "runtime": "vendor-local/librkllmrt.so",
               "bridge": "build/liblocalai-rkllm130.so", "models": records}
    # Validate a temporary candidate before creating the final config.
    temporary = ROOT / "config.preparing.json"
    if temporary.exists():
        raise SystemExit("Previous config.preparing.json exists; inspect it first")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2, allow_nan=False)
        load_settings(temporary)
        if not (ROOT / payload["bridge"]).is_file():
            raise ValueError("Candidate bridge missing; extract the complete board package")
        temporary.rename(config)
    finally:
        if temporary.exists():
            temporary.unlink()
    print("CANDIDATE_CONFIG_OK runtime=1.3.0 git_blob=" + info["git_blob"])
    print("Models:", ", ".join(r["name"] for r in records))
    print("No running service, startup script, model or tokenizer was changed.")


if __name__ == "__main__":
    main()
