"""Check packaged files against SHA-256 manifest, without loading model code."""
import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def verify(root=ROOT):
    manifest = json.loads((root / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
    files = manifest["files"]
    for relative, expected in files.items():
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or "\\" in relative:
            raise ValueError("Invalid manifest path")
        target = root.joinpath(*path.parts)
        if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
            raise ValueError("Unexpected package symlink")
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ValueError("Package file differs: " + relative)
    print("PACKAGE_OK files={} kind={}".format(len(files), manifest["kind"]))
    return manifest


if __name__ == "__main__":
    verify()
