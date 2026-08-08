"""Minimal, non-mutating credential loading for provider canaries.

The experiment never copies secrets into its JSON configuration.  This module
reads a deliberately supplied dotenv file, overlays the current process
environment, and returns a container whose representation is redacted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse


_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class CredentialError(ValueError):
    """Raised for missing or malformed non-secret provider configuration."""


@dataclass(frozen=True, repr=False)
class ProviderCredentials:
    """Provider settings with a representation that cannot disclose the key."""

    base_url: str
    model: str
    api_key: str

    def __repr__(self) -> str:
        return (
            "ProviderCredentials("
            f"base_url={self.base_url!r}, model={self.model!r}, api_key=<redacted>)"
        )

    __str__ = __repr__

    def public_metadata(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "credential_present": bool(self.api_key),
        }


def load_dotenv(path: str | Path) -> dict[str, str]:
    """Parse a small dotenv subset without mutating ``os.environ``.

    Values are treated literally: shell expansion and command substitution are
    intentionally unsupported. Error messages identify only line/key metadata,
    never a parsed value.
    """

    source = Path(path)
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CredentialError(f"cannot read environment file {source}: {exc}") from exc

    values: dict[str, str] = {}
    for line_number, original in enumerate(lines, start=1):
        line = original.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, raw_value = line.partition("=")
        name = name.strip()
        if not separator or not _ENV_NAME.fullmatch(name):
            raise CredentialError(f"invalid environment assignment at line {line_number}")
        if name in values:
            raise CredentialError(f"duplicate environment variable {name!r}")
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name] = value
    return values


def load_provider_credentials(
    *,
    prefix: str,
    env_file: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ProviderCredentials:
    """Load ``<PREFIX>_{BASE_URL,MODEL,API_KEY}`` with process-env precedence."""

    if not _ENV_NAME.fullmatch(prefix):
        raise CredentialError("provider prefix must be an environment-style name")
    merged = load_dotenv(env_file) if env_file is not None else {}
    merged.update(dict(os.environ if environ is None else environ))
    names = {
        "base_url": f"{prefix}_BASE_URL",
        "model": f"{prefix}_MODEL",
        "api_key": f"{prefix}_API_KEY",
    }
    missing = [name for name in names.values() if not merged.get(name, "").strip()]
    if missing:
        raise CredentialError("missing provider environment variables: " + ", ".join(missing))

    base_url = merged[names["base_url"]].strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CredentialError(f"{names['base_url']} must be an absolute HTTP(S) URL")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise CredentialError(f"{names['base_url']} must use HTTPS outside localhost")

    return ProviderCredentials(
        base_url=base_url,
        model=merged[names["model"]].strip(),
        api_key=merged[names["api_key"]].strip(),
    )


__all__ = [
    "CredentialError",
    "ProviderCredentials",
    "load_dotenv",
    "load_provider_credentials",
]
