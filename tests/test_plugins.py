from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omapath.actions import ActionRegistry
from omapath.plugins import (
    PLUGIN_API_VERSION,
    PluginMenuItem,
    PluginRegistry,
    discover_manifests,
    load_plugins,
)


class PluginApiTests(unittest.TestCase):
    def _write_plugin(self, root: Path, plugin_id: str, source: str, *, api_version: int = PLUGIN_API_VERSION) -> None:
        directory = root / plugin_id
        directory.mkdir()
        (directory / "plugin.toml").write_text(
            f'id = "{plugin_id}"\n'
            f'name = "{plugin_id.title()}"\n'
            'version = "1.0"\n'
            f"api_version = {api_version}\n"
            'entrypoint = "plugin.py:setup"\n',
            encoding="utf-8",
        )
        (directory / "plugin.py").write_text(source, encoding="utf-8")

    def test_plugin_actions_and_context_providers_are_available(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            self._write_plugin(
                root,
                "hello",
                """
from omapath.plugins import PluginMenuItem
calls = []
def setup(api):
    api.register_action('hello', 'Hello', lambda: calls.append('called'))
    api.register_context_menu_provider(lambda context: [PluginMenuItem('Plugin action', lambda: calls.append(context['location']))])
""",
            )
            registry = PluginRegistry(ActionRegistry())
            report = load_plugins(registry, root=root)
            self.assertEqual(report.loaded, ["hello"])
            self.assertEqual(report.failed, {})
            registry.actions.execute("plugin.hello.hello")
            module = registry.loaded_modules["hello"]
            self.assertEqual(module.calls, ["called"])
            items = registry.context_menu_items({"location": "/tmp"})
            self.assertEqual([item.label for item in items], ["Plugin action"])
            self.assertTrue(registry.invoke_menu_item(items[0]))
            self.assertEqual(module.calls[-1], "/tmp")

    def test_broken_plugin_isolated_and_safe_mode_skips_code(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            self._write_plugin(
                root,
                "broken",
                "def setup(api):\n    api.register_action('partial', 'Partial', lambda: None)\n    raise RuntimeError('boom')\n",
            )
            self._write_plugin(root, "oldapi", "def setup(api):\n    raise AssertionError('must not run')\n", api_version=99)
            self.assertEqual([manifest.plugin_id for manifest in discover_manifests(root)], ["broken", "oldapi"])

            registry = PluginRegistry(ActionRegistry())
            report = load_plugins(registry, root=root)
            self.assertEqual(report.loaded, [])
            self.assertIn("broken", report.failed)
            self.assertIn("oldapi", report.failed)
            self.assertNotIn("plugin.broken.partial", registry.actions.ids())

            safe_registry = PluginRegistry(ActionRegistry())
            safe_report = load_plugins(safe_registry, root=root, safe_mode=True)
            self.assertEqual(safe_report.skipped, ["broken", "oldapi"])
            self.assertEqual(safe_report.failed, {})
            self.assertEqual(safe_registry.loaded_modules, {})

    def test_manifest_entrypoint_cannot_escape_plugin_directory(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            self._write_plugin(root, "escape", "def setup(api):\n    pass\n")
            manifest = root / "escape" / "plugin.toml"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace("plugin.py:setup", "../outside.py:setup"),
                encoding="utf-8",
            )
            (root / "outside.py").write_text("def setup(api):\n    raise AssertionError('unsafe')\n", encoding="utf-8")
            report = load_plugins(PluginRegistry(ActionRegistry()), root=root)
            self.assertIn("escape", report.failed)


if __name__ == "__main__":
    unittest.main()
