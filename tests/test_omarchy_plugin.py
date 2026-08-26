from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifest.json"
LIFECYCLE = ROOT / "scripts" / "omapath-plugin"
VALIDATOR = ROOT / "scripts" / "validate_plugin.py"


class OmarchyPluginTests(unittest.TestCase):
    def test_manifest_points_to_a_real_bar_widget(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(manifest["id"], "io.github.pluseight8.omapath")
        self.assertEqual(manifest["kinds"], ["bar-widget"])
        self.assertEqual(manifest["entryPoints"]["barWidget"], "BarWidget.qml")
        self.assertTrue((ROOT / manifest["entryPoints"]["barWidget"]).is_file())

    def test_repository_validator_accepts_checkout(self) -> None:
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), str(ROOT)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("io.github.pluseight8.omapath", result.stdout)

    def test_repository_validator_rejects_unsafe_entry_point(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copy_root = Path(temporary)
            (copy_root / "BarWidget.qml").write_text("Item {}\n", encoding="utf-8")
            (copy_root / "README.md").write_text("plugin\n", encoding="utf-8")
            (copy_root / "LICENSE").write_text("MIT\n", encoding="utf-8")
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            manifest["entryPoints"]["barWidget"] = "../outside.qml"
            (copy_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(VALIDATOR), str(copy_root)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe entry point", result.stderr)

    def test_lifecycle_dry_run_is_non_mutating(self) -> None:
        result = subprocess.run(
            [str(LIFECYCLE), "install", "--dry-run"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("omarchy plugin add", result.stdout)
        self.assertIn("--enable", result.stdout)
        self.assertNotIn("desktop", result.stdout.lower())

    def test_lifecycle_scripts_have_valid_bash_syntax(self) -> None:
        for script in (LIFECYCLE, ROOT / "install.sh", ROOT / "update.sh", ROOT / "uninstall.sh"):
            result = subprocess.run(["bash", "-n", str(script)], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, f"{script}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
