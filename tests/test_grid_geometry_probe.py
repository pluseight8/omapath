from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

try:
    import gi

    gi.require_version("Gdk", "4.0")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gtk

    GTK_IMPORT_ERROR: Exception | None = None
except (ImportError, ValueError) as error:  # pragma: no cover - host dependent
    Gdk = None  # type: ignore[assignment]
    Gtk = None  # type: ignore[assignment]
    GTK_IMPORT_ERROR = error


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROBE = PROJECT_ROOT / "scripts" / "grid_geometry_probe.py"
COUNT_RE = re.compile(
    r"^count=(?P<count>\d+) .*?"
    r"vadj=\(lower=(?P<lower>[\d.]+) upper=(?P<upper>[\d.]+) "
    r"page_size=(?P<page>[\d.]+) value=(?P<value>[\d.]+)\) "
    r"realized_list_items=(?P<realized>\d+) "
    r"estimated_columns=(?P<columns>\d+) estimated_rows=(?P<rows>\d+) "
    r"item_samples=(?P<samples>\[[^\]]*\])"
)


class GridGeometryProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if GTK_IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"GTK4/PyGObject unavailable: {GTK_IMPORT_ERROR}")
        Gtk.init()
        if Gdk.Display.get_default() is None:
            raise unittest.SkipTest("no graphical display")

    def test_plain_grid_has_bounded_virtualized_geometry(self) -> None:
        environment = os.environ.copy()
        completed = subprocess.run(
            [sys.executable, str(PROBE)],
            cwd=PROJECT_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        output = f"{completed.stdout}\n{completed.stderr}"
        self.assertEqual(completed.returncode, 0, output)
        self.assertNotIn("Gtk-CRITICAL", output)
        self.assertNotIn("Gtk-WARNING", output)
        self.assertNotIn("Traceback", output)

        measurements: dict[int, dict[str, int | float | str]] = {}
        for line in completed.stdout.splitlines():
            match = COUNT_RE.match(line)
            if match is None:
                continue
            values: dict[str, int | float | str] = {
                "count": int(match.group("count")),
                "upper": float(match.group("upper")),
                "page": float(match.group("page")),
                "realized": int(match.group("realized")),
                "columns": int(match.group("columns")),
                "rows": int(match.group("rows")),
                "samples": match.group("samples"),
            }
            measurements[int(values["count"])] = values

        self.assertEqual(set(measurements), {10, 20, 100, 1000, 4000})
        for count, measurement in measurements.items():
            self.assertGreater(measurement["columns"], 0)
            self.assertGreater(measurement["rows"], 0)
            self.assertGreater(measurement["page"], 0)
            self.assertGreater(measurement["upper"], 0)
            self.assertNotEqual(measurement["samples"], "[]")
            # The probe's sample cells must retain real natural geometry.
            self.assertRegex(measurement["samples"], r"\(.*?, .*?, [1-9]\d*, [1-9]\d*\)")
            if count >= 1000:
                self.assertLess(measurement["realized"], count)
                # A cell row must remain bounded; this catches the old
                # one-column/viewport feedback loop without hard-coding a
                # particular compositor window size.
                self.assertLess(measurement["upper"], measurement["rows"] * 256 + 2048)

        self.assertGreater(measurements[20]["columns"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
