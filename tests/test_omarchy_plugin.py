from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifest.json"
LIFECYCLE = ROOT / "scripts" / "omapath-plugin"
DESKTOP_HELPER = ROOT / "scripts" / "desktop-entry.sh"
VALIDATOR = ROOT / "scripts" / "validate_plugin.py"


class OmarchyPluginTests(unittest.TestCase):
    def test_manifest_is_service_only_and_points_to_real_service(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(manifest["id"], "io.github.pluseight8.omapath")
        self.assertEqual(manifest["kinds"], ["service"])
        self.assertTrue(manifest["keepLoaded"])
        self.assertEqual(manifest["entryPoints"]["service"], "Service.qml")
        self.assertTrue((ROOT / manifest["entryPoints"]["service"]).is_file())
        self.assertFalse((ROOT / "BarWidget.qml").exists())

    def test_service_only_qml_maintains_launcher_without_bar_surface(self) -> None:
        source = (ROOT / "Service.qml").read_text(encoding="utf-8")
        self.assertIn("desktop-entry.sh", source)
        self.assertIn('"install"', source)
        self.assertNotIn("BarWidget", source)

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
            (copy_root / "Service.qml").write_text("Item {}\n", encoding="utf-8")
            (copy_root / "README.md").write_text("plugin\n", encoding="utf-8")
            (copy_root / "LICENSE").write_text("MIT\n", encoding="utf-8")
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            manifest["entryPoints"]["service"] = "../outside.qml"
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
        self.assertIn("desktop-entry", result.stdout)
        self.assertNotIn("bar widget", result.stdout.lower())

    def test_desktop_helper_is_marker_guarded_and_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            home = temporary_root / "home"
            data_home = temporary_root / "data"
            home.mkdir()
            environment = {
                **os.environ,
                "HOME": str(home),
                "XDG_DATA_HOME": str(data_home),
            }
            install = subprocess.run(
                [str(DESKTOP_HELPER), "install"],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(install.returncode, 0, install.stderr)
            desktop_file = data_home / "applications" / "io.github.pluseight8.omapath.desktop"
            self.assertTrue(desktop_file.is_file())
            content = desktop_file.read_text(encoding="utf-8")
            self.assertIn("X-Omapath-Plugin-Id=io.github.pluseight8.omapath", content)
            self.assertIn('Actions=new-window;', content)
            if shutil.which("desktop-file-validate") is None:
                self.skipTest("desktop-file-validate is not installed")
            validator = subprocess.run(
                ["desktop-file-validate", str(desktop_file)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(validator.returncode, 0, validator.stderr)

            desktop_file.write_text("[Desktop Entry]\nName=User file\n", encoding="utf-8")
            conflict = subprocess.run(
                [str(DESKTOP_HELPER), "install"],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(conflict.returncode, 0)
            self.assertEqual(desktop_file.read_text(encoding="utf-8"), "[Desktop Entry]\nName=User file\n")

            desktop_file.write_text(content, encoding="utf-8")
            remove = subprocess.run(
                [str(DESKTOP_HELPER), "remove"],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(remove.returncode, 0, remove.stderr)
            self.assertFalse(desktop_file.exists())

    def test_lifecycle_scripts_have_valid_bash_syntax(self) -> None:
        for script in (
            LIFECYCLE,
            DESKTOP_HELPER,
            ROOT / "install.sh",
            ROOT / "update.sh",
            ROOT / "uninstall.sh",
        ):
            result = subprocess.run(["bash", "-n", str(script)], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, f"{script}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
