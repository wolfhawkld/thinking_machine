"""Canonical source-tree provenance for paid experiment attempts."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ROOT_FILES = (
    ".gitignore",
    "README.md",
    "entropy-cycling-scientific-agent-position-paper.md",
    "experiment-spec.md",
    "paper-outline.md",
    "provider-integration.md",
    "pyproject.toml",
    "spark-to-knowledge-experiment-plan.md",
    "v3-development-spec.md",
)
_TREE_SUFFIXES = {".py", ".json"}
_PROTOCOL_DIRECTORIES = ("configs", "src", "tests")


class ProvenanceError(RuntimeError):
    """Raised when the executable source manifest cannot be frozen safely."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _protocol_files(root: Path) -> list[Path]:
    files: set[Path] = set()
    for relative in _ROOT_FILES:
        path = root / relative
        if path.is_file():
            files.add(path)
    for directory_name in _PROTOCOL_DIRECTORIES:
        directory = root / directory_name
        if not directory.is_dir():
            raise ProvenanceError(f"missing protocol directory: {directory_name}")
        for path in directory.rglob("*"):
            if path.is_symlink():
                raise ProvenanceError(
                    f"protocol manifest refuses symlink: {path.relative_to(root)}"
                )
            if path.is_file() and path.suffix in _TREE_SUFFIXES:
                files.add(path)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def protocol_git_pathspecs() -> tuple[str, ...]:
    """Return Git pathspecs covering exactly the source-manifest file scope."""

    tree_patterns = tuple(
        f":(top,glob){directory}/**/*{suffix}"
        for directory in _PROTOCOL_DIRECTORIES
        for suffix in sorted(_TREE_SUFFIXES)
    )
    return (*_ROOT_FILES, *tree_patterns)


def _git_head(root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return value or None


def source_manifest(root: str | Path = PROJECT_ROOT) -> dict[str, Any]:
    """Hash every executable/config/test protocol file plus environment metadata."""

    project_root = Path(root).resolve()
    entries: list[dict[str, Any]] = []
    for path in _protocol_files(project_root):
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ProvenanceError(
                f"cannot read protocol file {path.relative_to(project_root)}"
            ) from exc
        entries.append(
            {
                "path": path.relative_to(project_root).as_posix(),
                "size_bytes": len(payload),
                "sha256": _sha256_bytes(payload),
            }
        )
    if not entries:
        raise ProvenanceError("protocol source manifest cannot be empty")
    manifest_hash = _sha256_bytes(_canonical_json(entries))
    return {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_manifest_sha256": manifest_hash,
        "files": entries,
        "environment": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "python_executable": sys.executable,
            "platform": platform.platform(),
            "git_head": _git_head(project_root),
        },
    }


__all__ = [
    "PROJECT_ROOT",
    "ProvenanceError",
    "protocol_git_pathspecs",
    "source_manifest",
]
