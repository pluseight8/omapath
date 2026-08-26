from __future__ import annotations

import unittest

from omapath.remote import build_remote_uri, parse_remote_uri, supported_scheme


class RemoteTests(unittest.TestCase):
    def test_supported_uri_is_round_trippable_without_password_storage(self) -> None:
        uri = build_remote_uri("sftp", "example.test", "/data/файлы", "alice")
        self.assertEqual(uri, "sftp://alice@example.test/data/файлы")
        parsed = parse_remote_uri(uri)
        self.assertEqual(parsed.scheme, "sftp")  # type: ignore[union-attr]
        self.assertEqual(parsed.user, "alice")  # type: ignore[union-attr]
        self.assertTrue(supported_scheme(uri))

    def test_unsupported_or_malformed_hosts_are_rejected(self) -> None:
        self.assertFalse(supported_scheme("kio://example"))
        self.assertIsNone(parse_remote_uri("sftp:///missing-host"))
        with self.assertRaises(ValueError):
            build_remote_uri("sftp", "bad/host")


if __name__ == "__main__":
    unittest.main()
