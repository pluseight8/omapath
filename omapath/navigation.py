"""Navigation contracts independent from GTK widgets."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class NavigationHistory:
    current: str | None = None
    back: list[str] = field(default_factory=list)
    forward: list[str] = field(default_factory=list)

    def visit(self, location: str) -> None:
        if self.current == location:
            return
        if self.current is not None:
            self.back.append(self.current)
        self.current = location
        self.forward.clear()

    def go_back(self) -> str | None:
        if not self.back:
            return None
        if self.current is not None:
            self.forward.append(self.current)
        self.current = self.back.pop()
        return self.current

    def go_forward(self) -> str | None:
        if not self.forward:
            return None
        if self.current is not None:
            self.back.append(self.current)
        self.current = self.forward.pop()
        return self.current

    def clear(self) -> None:
        self.current = None
        self.back.clear()
        self.forward.clear()

