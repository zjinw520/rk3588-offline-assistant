"""Record hashes of the application source snapshot; no runtime data included."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {'.py','.qml','.js','.cpp','.h','.pro','.sh','.svg'}


def code_files(root=ROOT):
    return sorted(p for p in (root/'components').rglob('*') if p.is_file()
                  and (p.suffix in SUFFIXES or p.name=='S99rkllama_wake'))


def main():
    files={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in code_files()}
    value={'snapshot_date':'2026-11-09','scope':'project application source; excludes model/runtime binaries and personal data','files':files}
    (ROOT/'components/SOURCE_MANIFEST.json').write_text(json.dumps(value,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print('COMPONENT_MANIFEST_OK',len(files))


if __name__=='__main__':main()
