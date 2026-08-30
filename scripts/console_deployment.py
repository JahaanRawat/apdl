#!/usr/bin/env python3
"""Provision durable local APDL Console deployment metadata."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

DEPLOYMENT_ID_NAME = "APDL_DEPLOYMENT_ID"
DISPLAY_NAME = "APDL_DISPLAY_NAME"
BACKEND_VERSION = "APDL_BACKEND_VERSION"
BUILD_REVISION = "APDL_BUILD_REVISION"
DEFAULT_DISPLAY_NAME = "Local APDL"

SEMVER_PATTERN = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
FULL_GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class DeploymentConfigurationError(ValueError):
    """A local deployment metadata source is ambiguous or malformed."""


def generate_deployment_id() -> str:
    """Return a fresh canonical per-install deployment UUID."""
    return str(uuid.uuid4())


def validate_deployment_id(value: str) -> None:
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise DeploymentConfigurationError(
            f"{DEPLOYMENT_ID_NAME} must be a canonical UUID"
        ) from exc
    if parsed.int == 0 or str(parsed) != value:
        raise DeploymentConfigurationError(
            f"{DEPLOYMENT_ID_NAME} must be a canonical non-nil UUID"
        )


def validate_display_name(value: str) -> None:
    if (
        not value
        or value != value.strip()
        or len(value) > 100
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise DeploymentConfigurationError(
            f"{DISPLAY_NAME} must contain 1-100 normalized printable characters"
        )


def validate_backend_version(value: str) -> None:
    if SEMVER_PATTERN.fullmatch(value) is None:
        raise DeploymentConfigurationError(
            f"{BACKEND_VERSION} must be canonical SemVer"
        )


def validate_build_revision(value: str) -> None:
    if FULL_GIT_REVISION_PATTERN.fullmatch(value) is None:
        raise DeploymentConfigurationError(
            f"{BUILD_REVISION} must be a full lowercase 40-character Git revision"
        )


def release_version(manifest_path: Path) -> str:
    """Read the current backend version from the canonical release manifest."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentConfigurationError(
            "release-manifest.json could not be read"
        ) from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise DeploymentConfigurationError(
            "release-manifest.json must use schema version 1"
        )
    version = manifest.get("version")
    if not isinstance(version, str):
        raise DeploymentConfigurationError(
            "release-manifest.json version must be a string"
        )
    validate_backend_version(version)
    if manifest.get("tag") != f"v{version}":
        raise DeploymentConfigurationError(
            "release-manifest.json tag must match its version"
        )
    return version


def repository_revision(repository_root: Path) -> str:
    """Resolve a real commit object for the checked-out local source tree."""
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(repository_root),
                "rev-parse",
                "--verify",
                "HEAD^{commit}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise DeploymentConfigurationError(
            "APDL_BUILD_REVISION requires a Git checkout with a committed HEAD"
        ) from exc
    revision = completed.stdout.strip()
    validate_build_revision(revision)
    return revision


def ensure_local_configuration(
    env_file: Path,
    *,
    version: str,
    revision: str,
    deployment_id_factory: Callable[[], str] = generate_deployment_id,
) -> frozenset[str]:
    """Create or refresh canonical metadata without rotating deployment identity."""
    validate_backend_version(version)
    validate_build_revision(revision)
    try:
        source = env_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeploymentConfigurationError(
            "environment file could not be read; run make setup first"
        ) from exc
    lines = source.splitlines(keepends=True)
    names = (DEPLOYMENT_ID_NAME, DISPLAY_NAME, BACKEND_VERSION, BUILD_REVISION)
    locations: dict[str, int | None] = {}
    existing: dict[str, str] = {}

    for name in names:
        prefix = f"{name}="
        assignments = [
            index for index, line in enumerate(lines) if line.startswith(prefix)
        ]
        if len(assignments) > 1:
            raise DeploymentConfigurationError(f"{name} must be assigned exactly once")
        index = assignments[0] if assignments else None
        locations[name] = index
        existing[name] = (
            ""
            if index is None
            else lines[index].removesuffix("\n").removesuffix("\r")[len(prefix) :]
        )

    deployment_id = existing[DEPLOYMENT_ID_NAME]
    if deployment_id:
        validate_deployment_id(deployment_id)
    else:
        deployment_id = deployment_id_factory()
        validate_deployment_id(deployment_id)

    display_name = existing[DISPLAY_NAME] or DEFAULT_DISPLAY_NAME
    validate_display_name(display_name)
    desired = {
        DEPLOYMENT_ID_NAME: deployment_id,
        DISPLAY_NAME: display_name,
        BACKEND_VERSION: version,
        BUILD_REVISION: revision,
    }
    changed: set[str] = set()
    for name, value in desired.items():
        if existing[name] == value:
            continue
        assignment = f"{name}={value}\n"
        index = locations[name]
        if index is None:
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] = f"{lines[-1]}\n"
            lines.append(assignment)
        else:
            lines[index] = assignment
        changed.add(name)

    if not changed:
        return frozenset()

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=env_file.parent,
            prefix=f".{env_file.name}.",
            delete=False,
        ) as temporary:
            temporary.write("".join(lines))
            temporary_path = Path(temporary.name)
        temporary_path.chmod(0o600)
        os.replace(temporary_path, env_file)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return frozenset(changed)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    ensure = commands.add_parser(
        "ensure",
        help="persist the current checkout metadata in a local environment file",
    )
    ensure.add_argument("env_file", type=Path)
    ensure.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    root = arguments.repository_root.resolve()
    try:
        version = release_version(root / "release-manifest.json")
        revision = repository_revision(root)
        changed = ensure_local_configuration(
            arguments.env_file,
            version=version,
            revision=revision,
        )
    except (DeploymentConfigurationError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    state = ", ".join(sorted(changed)) if changed else "existing metadata"
    print(f"provisioned {state} in {arguments.env_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
