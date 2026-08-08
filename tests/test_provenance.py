from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from src.provenance import ProvenanceError, source_manifest


class ProvenanceTests(unittest.TestCase):
    def make_tree(self, root: Path) -> None:
        for directory in ("src", "tests", "configs"):
            (root / directory).mkdir()
        (root / "src" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "tests" / "test_module.py").write_text("pass\n", encoding="utf-8")
        (root / "configs" / "gate.json").write_text("{}\n", encoding="utf-8")
        (root / "README.md").write_text("protocol\n", encoding="utf-8")

    def test_manifest_is_stable_until_a_protocol_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_tree(root)
            first = source_manifest(root)
            second = source_manifest(root)
            self.assertEqual(
                first["source_manifest_sha256"],
                second["source_manifest_sha256"],
            )
            (root / "src" / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
            changed = source_manifest(root)

        self.assertNotEqual(
            first["source_manifest_sha256"],
            changed["source_manifest_sha256"],
        )
        self.assertEqual(
            [entry["path"] for entry in first["files"]],
            ["README.md", "configs/gate.json", "src/module.py", "tests/test_module.py"],
        )

    def test_manifest_refuses_missing_protocol_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ProvenanceError, "missing protocol"):
                source_manifest(directory)


if __name__ == "__main__":
    unittest.main()
