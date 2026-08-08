from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from src.credentials import (
    CredentialError,
    load_dotenv,
    load_provider_credentials,
)


class CredentialTests(unittest.TestCase):
    def write_env(self, directory: str, text: str) -> Path:
        path = Path(directory) / "provider.env"
        path.write_text(text, encoding="utf-8")
        return path

    def test_dotenv_parsing_is_literal_and_non_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_env(
                directory,
                "# local only\nexport DEEPSEEK_BASE_URL=https://example.test/\n"
                "DEEPSEEK_MODEL='model-x'\nDEEPSEEK_API_KEY=secret-value\n",
            )
            before = os.environ.get("DEEPSEEK_API_KEY")
            values = load_dotenv(path)

        self.assertEqual(values["DEEPSEEK_MODEL"], "model-x")
        self.assertEqual(values["DEEPSEEK_API_KEY"], "secret-value")
        self.assertEqual(os.environ.get("DEEPSEEK_API_KEY"), before)

    def test_credentials_are_redacted_and_process_environment_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_env(
                directory,
                "DEEPSEEK_BASE_URL=https://file.example/\n"
                "DEEPSEEK_MODEL=file-model\nDEEPSEEK_API_KEY=file-secret\n",
            )
            credentials = load_provider_credentials(
                prefix="DEEPSEEK",
                env_file=path,
                environ={"DEEPSEEK_MODEL": "process-model"},
            )

        self.assertEqual(credentials.base_url, "https://file.example")
        self.assertEqual(credentials.model, "process-model")
        self.assertEqual(credentials.api_key, "file-secret")
        self.assertNotIn("file-secret", repr(credentials))
        self.assertNotIn("file-secret", str(credentials))
        self.assertEqual(
            credentials.public_metadata(),
            {
                "base_url": "https://file.example",
                "model": "process-model",
                "credential_present": True,
            },
        )

    def test_errors_never_include_secret_values(self) -> None:
        secret = "do-not-leak-this-value"
        with self.assertRaises(CredentialError) as caught:
            load_provider_credentials(
                prefix="DEEPSEEK",
                environ={
                    "DEEPSEEK_BASE_URL": "not-a-url",
                    "DEEPSEEK_MODEL": "model-x",
                    "DEEPSEEK_API_KEY": secret,
                },
            )
        self.assertNotIn(secret, str(caught.exception))

    def test_missing_duplicate_and_insecure_remote_config_are_rejected(self) -> None:
        with self.assertRaisesRegex(CredentialError, "missing provider"):
            load_provider_credentials(prefix="DEEPSEEK", environ={})

        with tempfile.TemporaryDirectory() as directory:
            duplicate = self.write_env(directory, "A=1\nA=2\n")
            with self.assertRaisesRegex(CredentialError, "duplicate"):
                load_dotenv(duplicate)

        with self.assertRaisesRegex(CredentialError, "HTTPS"):
            load_provider_credentials(
                prefix="DEEPSEEK",
                environ={
                    "DEEPSEEK_BASE_URL": "http://provider.example",
                    "DEEPSEEK_MODEL": "model-x",
                    "DEEPSEEK_API_KEY": "secret",
                },
            )


if __name__ == "__main__":
    unittest.main()
