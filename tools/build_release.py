"""Allowlist source/board packages; never collect models, logs or environments."""
import hashlib
import argparse
import io
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_ai.elf import inspect_elf


def source_files():
    names = {".gitignore", ".gitattributes", "README.md", "LICENSE", "THIRD_PARTY.md", "CONTRIBUTING.md", "requirements.txt",
             "config.example.json", "run_candidate.sh", "native/bridge.cpp",
             "native/compat/cstdint", "tests/mock_runtime.cpp",
             "third_party/rockchip-1.3.0/include/rkllm.h",
             "third_party/rockchip-1.3.0/LICENSE", ".github/workflows/test.yml"}
    names.add(".github/ISSUE_TEMPLATE/bug_report.md")
    names.add("tests/fixtures/S99rkllama")
    for directory, pattern in [("local_ai", "*.py"), ("tools", "*.py"),
                               ("tests", "*.py"), ("docs", "*.md")]:
        names.update(path.relative_to(ROOT).as_posix() for path in (ROOT / directory).glob(pattern))
    suffixes={'.py','.qml','.js','.cpp','.h','.pro','.sh','.svg','.md','.txt'}
    for path in (ROOT/'components').rglob('*'):
        if path.is_file() and (path.suffix in suffixes or path.name in {'SOURCE_MANIFEST.json','S99rkllama_wake'}):
            names.add(path.relative_to(ROOT).as_posix())
    return sorted(names)


def package(kind, prefix, filename, names):
    files = {}
    output = ROOT / "dist" / filename
    output.parent.mkdir(exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for name in names:
            source = ROOT / name
            if source.is_symlink() or not source.is_file():
                raise ValueError("Invalid release input: " + name)
            data = source.read_bytes()
            files[name] = hashlib.sha256(data).hexdigest()
            info = tarfile.TarInfo(prefix + "/" + name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
        data = json.dumps({"version": "0.1.0", "kind": kind, "files": files},
                          sort_keys=True, indent=2).encode() + b"\n"
        info = tarfile.TarInfo(prefix + "/PACKAGE_MANIFEST.json")
        info.size, info.mode = len(data), 0o644
        archive.addfile(info, io.BytesIO(data))
    print("PACKAGE_OK", output.name, output.stat().st_size, "SHA256", hashlib.sha256(output.read_bytes()).hexdigest())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suffix", default="")
    args = parser.parse_args()
    if args.suffix and any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in args.suffix):
        parser.error("suffix must contain lowercase letters, digits or hyphens")
    suffix = "-" + args.suffix if args.suffix else ""
    names = source_files()
    package("source", "rk3588-offline-assistant", "local-ai-source-v010" + suffix + ".tar.gz", names)
    bridge = ROOT / "build/liblocalai-rkllm130.so"
    elf = inspect_elf(bridge)
    if elf["machine"] != 183 or elf["highest_glibc"] is None:
        raise ValueError("Expected verified AArch64 bridge")
    if tuple(map(int, elf["highest_glibc"].split("."))) > (2, 37):
        raise ValueError("Bridge requires newer glibc than board")
    print("BRIDGE_ELF", json.dumps(elf))
    package("board-candidate", "local-ai-candidate-v010", "local-ai-candidate-v010" + suffix + ".tar.gz",
            names + ["build/liblocalai-rkllm130.so"])
    package("aarch64-starter", "rk3588-offline-assistant", "rk3588-offline-assistant-v0.1.0-aarch64" + suffix + ".tar.gz",
            names + ["build/liblocalai-rkllm130.so"])


if __name__ == "__main__":
    main()
