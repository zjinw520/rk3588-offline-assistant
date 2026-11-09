"""Build our bridge; no Rockchip runtime binary is linked or bundled."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zig", help="Path to Zig 0.13.x executable")
    parser.add_argument("--cxx", help="Alternative AArch64 Linux C++ compiler")
    parser.add_argument("--host-mock", action="store_true", help="Windows test bridge/mock runtime, never for the board")
    args = parser.parse_args()
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    env = os.environ.copy()
    temp = build / "tmp"
    temp.mkdir(exist_ok=True)
    env.update(TMP=str(temp), TEMP=str(temp), ZIG_LOCAL_CACHE_DIR=str(build / "zig-local"),
               ZIG_GLOBAL_CACHE_DIR=str(build / "zig-global"))
    zig = args.zig or shutil.which("zig")
    flags = ["-std=c++11", "-O2", "-shared", "-fno-exceptions", "-fno-rtti",
             "-I", str(ROOT / "third_party/rockchip-1.3.0/include")]
    if zig:
        compiler = [str(Path(zig).resolve()), "c++", "-target",
                    "x86_64-windows-gnu" if args.host_mock else "aarch64-linux-gnu.2.17",
                    "-nostdlib++", "-I", str(ROOT / "native/compat")]
    else:
        if args.host_mock:
            parser.error("--host-mock requires Zig")
        compiler = [args.cxx or "aarch64-linux-gnu-g++"]
    if not args.host_mock:
        flags += ["-fPIC", "-fvisibility=hidden"]
    filename = "localai-bridge-test.dll" if args.host_mock else "liblocalai-rkllm130.so"
    output = build / filename
    subprocess.run(compiler + flags + [str(ROOT / "native/bridge.cpp")]
                   + ([] if args.host_mock else ["-ldl"]) + ["-o", str(output)],
                   cwd=ROOT, env=env, check=True)
    if args.host_mock:
        subprocess.run(compiler + flags + [str(ROOT / "tests/mock_runtime.cpp"),
                                          "-o", str(build / "mock-rkllm.dll")],
                       cwd=ROOT, env=env, check=True)
    print("BUILD_OK", output)


if __name__ == "__main__":
    main()
