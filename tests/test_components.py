import ast
import hashlib
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]


class ComponentSnapshotTests(unittest.TestCase):
    def test_snapshot_has_exact_file_hashes(self):
        manifest=json.loads((ROOT/'components/SOURCE_MANIFEST.json').read_text())
        self.assertGreaterEqual(len(manifest['files']),40)
        for name,sha in manifest['files'].items():
            path=ROOT/name
            self.assertTrue(path.resolve().is_relative_to(ROOT/'components'))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),sha,name)
    def test_snapshot_python_is_py310_and_does_not_import_rkllama(self):
        for path in (ROOT/'components').rglob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf-8'),feature_version=(3,10))
            for node in ast.walk(tree):
                names=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
                self.assertFalse(any(name.split('.')[0]=='rkllama' for name in names),str(path))
