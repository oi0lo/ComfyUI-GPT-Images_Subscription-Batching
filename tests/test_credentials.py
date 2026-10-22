import ctypes
import os
import sys
import unittest

import helpers  # noqa: F401
import credentials


class ResolveTests(unittest.TestCase):
    def test_environment_first(self):
        key, source = credentials.resolve_api_key({"OPENAI_API_KEY": " sk-env-123456 "}, lambda: "sk-store-1")
        self.assertEqual(key, "sk-env-123456")
        self.assertEqual(source, credentials.ENV_SOURCE)

    def test_store_when_no_environment(self):
        key, source = credentials.resolve_api_key({}, lambda: "sk-store-123456")
        self.assertEqual(key, "sk-store-123456")
        self.assertEqual(source, credentials.STORE_SOURCE)

    def test_bad_environment_key_does_not_fall_back(self):
        consulted = []
        with self.assertRaises(credentials.CredentialError) as caught:
            credentials.resolve_api_key({"OPENAI_API_KEY": "sk bad key"}, lambda: consulted.append(1) or "x")
        self.assertNotIsInstance(caught.exception, (credentials.NoKeyError, credentials.StoredKeyInvalid))
        self.assertIn("environment variable", str(caught.exception))
        self.assertEqual(consulted, [])

    def test_missing_key_is_its_own_error(self):
        with self.assertRaises(credentials.NoKeyError) as caught:
            credentials.resolve_api_key({}, lambda: None)
        self.assertIn("OPENAI_API_KEY", str(caught.exception))

    def test_exact_helper_command_on_windows(self):
        real = sys.platform
        try:
            sys.platform = "win32"
            message = credentials.setup_hint()
        finally:
            sys.platform = real
        self.assertIn(sys.executable, message)
        self.assertIn(os.path.join(credentials.PACKAGE_DIR, "setup_credentials.py"), message)
        self.assertIn(" set", message)

    def test_unusable_stored_key(self):
        with self.assertRaises(credentials.StoredKeyInvalid):
            credentials.resolve_api_key({}, lambda: "has spaces in it")

        def unreadable():
            raise credentials.CredentialError("cannot be read")

        with self.assertRaises(credentials.StoredKeyInvalid):
            credentials.resolve_api_key({}, unreadable)

    def test_syntax(self):
        for bad in ["", "a b", "tab\tkey", "ключ", "x" * 3000]:
            with self.assertRaises(credentials.CredentialError):
                credentials.check_syntax(bad)
        self.assertEqual(credentials.check_syntax("sk-proj-Abc_123-xyz"), "sk-proj-Abc_123-xyz")


class BlobTests(unittest.TestCase):
    def test_utf8_and_utf16_entries(self):
        self.assertEqual(credentials.decode_blob(b"sk-abc123"), "sk-abc123")
        self.assertEqual(credentials.decode_blob("sk-abc123".encode("utf-16-le")), "sk-abc123")

    def test_unreadable_entry(self):
        with self.assertRaises(credentials.CredentialError):
            credentials.decode_blob(b"\xff\xfe\xfa")


class LayoutTests(unittest.TestCase):
    @unittest.skipUnless(ctypes.sizeof(ctypes.c_void_p) == 8, "64-bit layout check")
    def test_credentialw_matches_win64_layout(self):
        s = credentials._CREDENTIALW
        self.assertEqual(ctypes.sizeof(s), 80)
        expected = {
            "Flags": 0, "Type": 4, "TargetName": 8, "Comment": 16, "LastWritten": 24,
            "CredentialBlobSize": 32, "CredentialBlob": 40, "Persist": 48, "AttributeCount": 52,
            "Attributes": 56, "TargetAlias": 64, "UserName": 72,
        }
        for name, offset in expected.items():
            self.assertEqual(getattr(s, name).offset, offset, name)


if __name__ == "__main__":
    unittest.main()
