"""Central action registry shared by menus, shortcuts and the command palette.

The registry is intentionally toolkit agnostic.  GTK adapters register a
callable and the presentation layer only asks the registry to execute an
action.  This keeps keyboard/menu implementations from growing separate file
operation paths and makes user shortcuts safe to validate and persist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable


ActionCallback = Callable[[], object]
EnabledPredicate = Callable[[], bool]


class ShortcutConflict(ValueError):
    """Raised when assigning a shortcut would shadow another action."""

    def __init__(self, shortcut: str, action_ids: Iterable[str]) -> None:
        self.shortcut = normalize_shortcut(shortcut)
        self.action_ids = tuple(action_ids)
        names = ", ".join(self.action_ids)
        super().__init__(f"{self.shortcut} is already assigned to {names}")


_MODIFIER_ALIASES = {
    "ctrl": "Ctrl",
    "control": "Ctrl",
    "ctl": "Ctrl",
    "alt": "Alt",
    "option": "Alt",
    "shift": "Shift",
    "super": "Super",
    "meta": "Super",
    "win": "Super",
    "mod4": "Super",
}
_KEY_ALIASES = {
    "return": "Enter",
    "kp_enter": "Enter",
    "escape": "Escape",
    "esc": "Escape",
    "space": "Space",
    "kp_add": "+",
    "plus": "+",
    "kp_subtract": "-",
    "minus": "-",
    "backspace": "Backspace",
    "del": "Delete",
}
_MODIFIER_ORDER = ("Ctrl", "Alt", "Shift", "Super")


def _canonical_key(value: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError("shortcut must include a key")
    folded = text.casefold()
    is_modifier_key = folded.endswith(("_l", "_r")) and folded[:-2] in _MODIFIER_ALIASES
    if folded in _MODIFIER_ALIASES or is_modifier_key:
        raise ValueError("shortcut must include a non-modifier key")
    alias = _KEY_ALIASES.get(text.casefold())
    if alias is not None:
        return alias
    if len(text) == 1:
        return text.upper() if text.isalpha() else text
    if text.casefold().startswith("kp_"):
        text = text[3:]
    return text[:1].upper() + text[1:]


def normalize_shortcut(value: str) -> str:
    """Return one stable human-readable shortcut representation.

    The parser accepts the forms used by GTK key names and by persisted
    settings, including ``Ctrl++`` and ``Ctrl+-``.  Empty strings mean that an
    action is intentionally unassigned.
    """

    raw = str(value or "").strip().replace(" ", "")
    if not raw:
        return ""
    if raw == "+":
        return "+"
    key_override: str | None = None
    if raw.endswith("++"):
        raw = raw[:-1]
        key_override = "+"
    elif raw.endswith("+-"):
        raw = raw[:-1]
        key_override = "-"
    parts = [part for part in raw.split("+") if part]
    if key_override is not None:
        parts.append(key_override)
    if not parts:
        raise ValueError("shortcut must include a key")
    modifiers: set[str] = set()
    for part in parts[:-1]:
        modifier = _MODIFIER_ALIASES.get(part.casefold())
        if modifier is None:
            raise ValueError(f"unknown shortcut modifier: {part}")
        modifiers.add(modifier)
    key = _canonical_key(parts[-1])
    return "+".join([modifier for modifier in _MODIFIER_ORDER if modifier in modifiers] + [key])


def shortcut_from_key_event(key_name: str, modifiers: Iterable[str] = ()) -> str:
    """Build a canonical shortcut from a toolkit key name and modifiers."""

    modifier_names = [_MODIFIER_ALIASES.get(str(value).casefold()) for value in modifiers]
    clean_modifiers = [value for value in modifier_names if value is not None]
    key = _canonical_key(key_name)
    return "+".join(
        [modifier for modifier in _MODIFIER_ORDER if modifier in clean_modifiers] + [key],
    )


@dataclass(slots=True)
class Action:
    """One user-facing command with a stable identifier."""

    action_id: str
    label: str
    callback: ActionCallback
    shortcut: str = ""
    category: str = "General"
    description: str = ""
    enabled: EnabledPredicate | None = None
    default_shortcut: str = ""

    def is_enabled(self) -> bool:
        if self.enabled is None:
            return True
        try:
            return bool(self.enabled())
        except Exception:
            # A stale UI predicate must never make the palette or menu crash.
            return False


class ActionRegistry:
    """Register and execute actions without duplicating command logic."""

    def __init__(self) -> None:
        self._actions: dict[str, Action] = {}

    def register(
        self,
        action_id: str,
        label: str,
        callback: ActionCallback,
        *,
        shortcut: str = "",
        category: str = "General",
        description: str = "",
        enabled: EnabledPredicate | None = None,
        replace: bool = False,
    ) -> Action:
        normalized = str(action_id).strip()
        if not normalized or "." not in normalized:
            raise ValueError("action_id must be a non-empty namespaced id")
        if not callable(callback):
            raise TypeError("action callback must be callable")
        if normalized in self._actions and not replace:
            raise ValueError(f"action already registered: {normalized}")
        action = Action(
            normalized,
            str(label),
            callback,
            normalize_shortcut(shortcut),
            str(category),
            str(description),
            enabled,
            normalize_shortcut(shortcut),
        )
        self._actions[normalized] = action
        return action

    def unregister(self, action_id: str) -> None:
        self._actions.pop(str(action_id), None)

    def get(self, action_id: str) -> Action | None:
        return self._actions.get(str(action_id))

    def require(self, action_id: str) -> Action:
        action = self.get(action_id)
        if action is None:
            raise KeyError(action_id)
        return action

    def execute(self, action_id: str) -> object:
        action = self.require(action_id)
        if not action.is_enabled():
            return None
        return action.callback()

    def set_shortcut(self, action_id: str, shortcut: str) -> None:
        self.assign_shortcut(action_id, shortcut)

    def shortcut_conflicts_for(self, shortcut: str, *, exclude: str | None = None) -> list[Action]:
        normalized = normalize_shortcut(shortcut)
        if not normalized:
            return []
        return [
            action
            for action in self._actions.values()
            if action.action_id != exclude and action.shortcut.casefold() == normalized.casefold()
        ]

    def assign_shortcut(self, action_id: str, shortcut: str, *, allow_replace: bool = False) -> None:
        """Assign a binding, optionally clearing intentional conflicts."""

        action = self.require(action_id)
        normalized = normalize_shortcut(shortcut)
        conflicts = self.shortcut_conflicts_for(normalized, exclude=action_id)
        if conflicts and not allow_replace:
            raise ShortcutConflict(normalized, [item.action_id for item in conflicts])
        for conflict in conflicts:
            conflict.shortcut = ""
        action.shortcut = normalized

    def reset_shortcut(self, action_id: str) -> None:
        action = self.require(action_id)
        self.assign_shortcut(action_id, action.default_shortcut, allow_replace=True)

    def reset_shortcuts(self) -> None:
        for action in self._actions.values():
            action.shortcut = action.default_shortcut

    def load_shortcuts(self, overrides: object) -> None:
        """Apply validated user overrides while keeping bindings conflict-free."""

        self.reset_shortcuts()
        if not isinstance(overrides, dict):
            return
        for action_id, shortcut in overrides.items():
            if action_id not in self._actions or not isinstance(shortcut, str):
                continue
            try:
                self.assign_shortcut(action_id, shortcut)
            except (ValueError, ShortcutConflict):
                # A malformed or colliding persisted binding must not make the
                # application unusable; the default remains active instead.
                continue

    def apply_shortcuts(self, values: object) -> None:
        """Apply a complete, conflict-free effective binding map atomically."""

        if not isinstance(values, dict):
            raise ValueError("shortcut values must be a mapping")
        candidate: dict[str, str] = {}
        owners: dict[str, str] = {}
        for action_id, shortcut in values.items():
            if action_id not in self._actions:
                continue
            if not isinstance(shortcut, str):
                raise ValueError(f"shortcut for {action_id} must be text")
            normalized = normalize_shortcut(shortcut)
            if normalized:
                owner = owners.get(normalized.casefold())
                if owner is not None and owner != action_id:
                    raise ShortcutConflict(normalized, [owner, action_id])
                owners[normalized.casefold()] = action_id
            candidate[action_id] = normalized
        self.reset_shortcuts()
        for action_id, shortcut in candidate.items():
            self._actions[action_id].shortcut = shortcut

    def user_shortcuts(self) -> dict[str, str]:
        """Return only bindings that differ from their defaults."""

        return {
            action.action_id: action.shortcut
            for action in self._actions.values()
            if action.shortcut != action.default_shortcut
        }

    def shortcut_conflicts(self) -> dict[str, list[str]]:
        """Return shortcuts assigned to more than one action."""

        grouped: dict[str, list[str]] = {}
        for action in self._actions.values():
            if action.shortcut:
                grouped.setdefault(action.shortcut.casefold(), []).append(action.action_id)
        return {shortcut: ids for shortcut, ids in grouped.items() if len(ids) > 1}

    def find_shortcut(self, shortcut: str) -> Action | None:
        try:
            needle = normalize_shortcut(shortcut).casefold()
        except ValueError:
            return None
        if not needle:
            return None
        for action in self._actions.values():
            if action.shortcut.casefold() == needle and action.is_enabled():
                return action
        return None

    def all(self, *, include_disabled: bool = True) -> list[Action]:
        actions = list(self._actions.values())
        if not include_disabled:
            actions = [action for action in actions if action.is_enabled()]
        return sorted(actions, key=lambda action: (action.category.casefold(), action.label.casefold(), action.action_id))

    def by_category(self, *, include_disabled: bool = True) -> dict[str, list[Action]]:
        result: dict[str, list[Action]] = {}
        for action in self.all(include_disabled=include_disabled):
            result.setdefault(action.category, []).append(action)
        return result

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._actions))

    def extend(self, actions: Iterable[Action]) -> None:
        for action in actions:
            registered = self.register(
                action.action_id,
                action.label,
                action.callback,
                shortcut=action.default_shortcut or action.shortcut,
                category=action.category,
                description=action.description,
                enabled=action.enabled,
            )
            registered.shortcut = normalize_shortcut(action.shortcut)


__all__ = [
    "Action",
    "ActionCallback",
    "ActionRegistry",
    "EnabledPredicate",
    "ShortcutConflict",
    "normalize_shortcut",
    "shortcut_from_key_event",
]
