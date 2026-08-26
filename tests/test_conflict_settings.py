from __future__ import annotations

import unittest
from unittest.mock import Mock

from omapath.operations import ConflictPolicy, OperationKind
from omapath.ui.simple_window import OmapathApplication


class ConflictSettingsTests(unittest.TestCase):
    @staticmethod
    def _host(behavior: str) -> object:
        host = type("Host", (), {})()
        host.settings = {"conflict_behavior": behavior}
        host._operation_conflicts = Mock(return_value=[("/tmp/incoming", "/tmp/existing")])
        host._begin_conflict_queue = Mock()
        host._submit_operation_now = Mock()
        return host

    def test_ask_keeps_the_interactive_conflict_queue(self) -> None:
        host = self._host("ask")

        OmapathApplication._submit_operation(host, OperationKind.COPY, ["/tmp/incoming"], "/tmp")

        host._begin_conflict_queue.assert_called_once()
        host._submit_operation_now.assert_not_called()

    def test_noninteractive_policy_is_applied_without_bypassing_the_worker(self) -> None:
        host = self._host("skip")

        OmapathApplication._submit_operation(host, OperationKind.COPY, ["/tmp/incoming"], "/tmp")

        host._begin_conflict_queue.assert_not_called()
        host._submit_operation_now.assert_called_once()
        self.assertEqual(host._submit_operation_now.call_args.kwargs["policy"], ConflictPolicy.SKIP)


if __name__ == "__main__":
    unittest.main()
