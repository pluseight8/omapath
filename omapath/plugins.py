"""Small, versioned extension surface for optional Omapath plugins.

Plugins are ordinary user Python code, not a security sandbox.  They are
loaded one at a time behind an exception boundary so a broken plugin cannot
prevent the core application from starting.  ``--safe-mode`` skips them all.
"""

from __future__ import annotations

import importlib.util
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Callable, Iterable

from .actions import ActionRegistry
from .diagnostics import LOGGER


PLUGIN_API_VERSION = 1
PLUGIN_ROOT = Path.home() / ".local/share/omapath/plugins"
_PLUGIN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")


@dataclass(frozen=True, slots=True)
class PluginManifest:
    plugin_id: str
    name: str
    version: str
    api_version: int
    entrypoint: str
    capabilities: tuple[str, ...] = ()
    manifest_path: Path | None = None

    @classmethod
    def from_path(cls, path: Path) -> PluginManifest:
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
        plugin_id = str(raw.get("id", "")).strip().casefold()
        if not _PLUGIN_ID.fullmatch(plugin_id):
            raise ValueError("plugin id must be 2-64 lowercase characters")
        name = str(raw.get("name", "")).strip()
        version = str(raw.get("version", "")).strip()
        entrypoint = str(raw.get("entrypoint", "")).strip()
        if not name or not version or not entrypoint or ":" not in entrypoint:
            raise ValueError("plugin manifest requires name, version and entrypoint=module.py:callable")
        try:
            api_version = int(raw.get("api_version", 0))
        except (TypeError, ValueError) as error:
            raise ValueError("api_version must be an integer") from error
        raw_capabilities = raw.get("capabilities", raw.get("permissions", []))
        capabilities = tuple(
            sorted(
                {
                    str(value).strip()
                    for value in raw_capabilities
                    if isinstance(value, str) and value.strip()
                }
            )
            if isinstance(raw_capabilities, list)
            else ()
        )
        return cls(plugin_id, name, version, api_version, entrypoint, capabilities, path)


@dataclass(frozen=True, slots=True)
class PluginMenuItem:
    """A safe context-menu contribution returned by a plugin provider."""

    label: str
    callback: Callable[[], object]
    enabled: bool = True
    category: str = "Plugins"


@dataclass(slots=True)
class PluginLoadReport:
    loaded: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)


class PluginContext:
    """The only object a plugin setup function needs to retain."""

    api_version = PLUGIN_API_VERSION

    def __init__(self, plugin_id: str, registry: PluginRegistry) -> None:
        self.plugin_id = plugin_id
        self._registry = registry

    def register_action(
        self,
        action_id: str,
        label: str,
        callback: Callable[[], object],
        *,
        shortcut: str = "",
        category: str = "Plugins",
        description: str = "",
    ) -> str:
        """Register an action under a collision-proof plugin namespace."""

        local_id = str(action_id).strip()
        if not local_id or "." in local_id:
            raise ValueError("plugin action_id must be a local name without dots")
        full_id = f"plugin.{self.plugin_id}.{local_id}"
        self._registry.actions.register(
            full_id,
            label,
            callback,
            shortcut=shortcut,
            category=category,
            description=description,
        )
        return full_id

    def register_context_menu_provider(self, provider: Callable[[object], Iterable[PluginMenuItem]]) -> None:
        self._registry.register_context_menu_provider(self.plugin_id, provider)

    def register_metadata_provider(self, provider: Callable[..., object]) -> None:
        self._registry.register_provider("metadata", self.plugin_id, provider)

    def register_preview_provider(self, provider: Callable[..., object]) -> None:
        self._registry.register_provider("preview", self.plugin_id, provider)

    def register_sidebar_provider(self, provider: Callable[..., object]) -> None:
        self._registry.register_provider("sidebar", self.plugin_id, provider)

    def register_vcs_provider(self, provider: Callable[..., object]) -> None:
        self._registry.register_provider("vcs", self.plugin_id, provider)

    def register_remote_provider(self, provider: Callable[..., object]) -> None:
        self._registry.register_provider("remote", self.plugin_id, provider)


class PluginRegistry:
    """Runtime-owned plugin contributions with failure-isolated providers."""

    def __init__(self, actions: ActionRegistry | None = None) -> None:
        self.actions = actions or ActionRegistry()
        self._providers: dict[str, list[tuple[str, Callable[..., object]]]] = {}
        self._context_menu_providers: list[tuple[str, Callable[[object], Iterable[PluginMenuItem]]]] = []
        self.loaded_modules: dict[str, ModuleType] = {}

    def register_provider(self, kind: str, plugin_id: str, provider: Callable[..., object]) -> None:
        if not callable(provider):
            raise TypeError(f"{kind} provider must be callable")
        self._providers.setdefault(str(kind), []).append((plugin_id, provider))

    def register_context_menu_provider(
        self,
        plugin_id: str,
        provider: Callable[[object], Iterable[PluginMenuItem]],
    ) -> None:
        if not callable(provider):
            raise TypeError("context-menu provider must be callable")
        self._context_menu_providers.append((plugin_id, provider))

    def remove_plugin(self, plugin_id: str) -> None:
        """Remove partial contributions after a plugin setup failure."""

        prefix = f"plugin.{plugin_id}."
        for action_id in tuple(self.actions.ids()):
            if action_id.startswith(prefix):
                self.actions.unregister(action_id)
        for kind, providers in tuple(self._providers.items()):
            self._providers[kind] = [item for item in providers if item[0] != plugin_id]
        self._context_menu_providers = [
            item for item in self._context_menu_providers if item[0] != plugin_id
        ]

    def context_menu_items(self, context: object) -> list[PluginMenuItem]:
        result: list[PluginMenuItem] = []
        for plugin_id, provider in tuple(self._context_menu_providers):
            try:
                values = provider(context)
                for item in values or ():
                    if isinstance(item, PluginMenuItem) and item.label:
                        result.append(item)
            except Exception as error:
                LOGGER.error("plugin context provider failed id=%s: %s", plugin_id, error)
        return result

    def invoke_menu_item(self, item: PluginMenuItem) -> bool:
        try:
            item.callback()
        except Exception as error:
            LOGGER.error("plugin context action failed label=%s: %s", item.label, error)
            return False
        return True

    def provider_values(self, kind: str, *args: object, **kwargs: object) -> list[object]:
        """Call optional providers without allowing one failure to cascade."""

        values: list[object] = []
        for plugin_id, provider in tuple(self._providers.get(kind, ())):
            try:
                value = provider(*args, **kwargs)
                if value is not None:
                    values.append(value)
            except Exception as error:
                LOGGER.error("plugin provider failed kind=%s id=%s: %s", kind, plugin_id, error)
        return values


def discover_manifests(root: Path | None = None) -> list[PluginManifest]:
    """Discover bounded, directory-based manifests without importing code."""

    plugin_root = root or PLUGIN_ROOT
    try:
        candidates = sorted(plugin_root.iterdir(), key=lambda path: path.name.casefold())
    except OSError:
        return []
    manifests: list[PluginManifest] = []
    for directory in candidates[:128]:
        if not directory.is_dir() or directory.is_symlink():
            continue
        manifest_path = directory / "plugin.toml"
        if not manifest_path.is_file():
            continue
        try:
            manifests.append(PluginManifest.from_path(manifest_path))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError, ValueError) as error:
            LOGGER.error("plugin manifest rejected path=%s: %s", manifest_path, error)
    return manifests


def _load_module(manifest: PluginManifest) -> ModuleType:
    if manifest.manifest_path is None:
        raise ValueError("plugin manifest path is missing")
    module_part, callable_name = manifest.entrypoint.split(":", 1)
    if not module_part or not callable_name or "." in callable_name:
        raise ValueError("entrypoint must be module.py:setup")
    plugin_directory = manifest.manifest_path.parent.resolve()
    module_path = (plugin_directory / module_part).resolve()
    if module_path.parent != plugin_directory or module_path.suffix != ".py" or not module_path.is_file():
        raise ValueError("entrypoint module must be a .py file inside its plugin directory")
    module_name = f"omapath_plugin_{manifest.plugin_id.replace('-', '_').replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load plugin module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    setup = getattr(module, callable_name, None)
    if not callable(setup):
        raise ValueError(f"plugin entrypoint is not callable: {manifest.entrypoint}")
    return module


def load_plugins(
    registry: PluginRegistry,
    *,
    root: Path | None = None,
    safe_mode: bool = False,
) -> PluginLoadReport:
    """Load every valid plugin independently and return an honest report."""

    report = PluginLoadReport()
    for manifest in discover_manifests(root):
        if safe_mode:
            report.skipped.append(manifest.plugin_id)
            continue
        if manifest.api_version != PLUGIN_API_VERSION:
            report.failed[manifest.plugin_id] = f"unsupported API version {manifest.api_version}"
            continue
        try:
            module = _load_module(manifest)
            setup = getattr(module, manifest.entrypoint.split(":", 1)[1])
            setup(PluginContext(manifest.plugin_id, registry))
        except Exception as error:
            registry.remove_plugin(manifest.plugin_id)
            report.failed[manifest.plugin_id] = str(error)
            LOGGER.error("plugin disabled for this session id=%s: %s", manifest.plugin_id, error)
            continue
        registry.loaded_modules[manifest.plugin_id] = module
        report.loaded.append(manifest.plugin_id)
    return report


__all__ = [
    "PLUGIN_API_VERSION",
    "PLUGIN_ROOT",
    "PluginContext",
    "PluginLoadReport",
    "PluginManifest",
    "PluginMenuItem",
    "PluginRegistry",
    "discover_manifests",
    "load_plugins",
]
