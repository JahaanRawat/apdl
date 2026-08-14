"""Contracts for durable local APDL Console deployment metadata."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import console_deployment

ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT_ID = "87fab7d6-dba0-4f77-8ffd-00e815fc7303"


class ConsoleDeploymentTests(unittest.TestCase):
    def test_ensure_generates_identity_once_and_refreshes_build_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "APDL_DEPLOYMENT_ID=\n"
                "APDL_DISPLAY_NAME=Local APDL\n"
                "APDL_BACKEND_VERSION=\n"
                "APDL_BUILD_REVISION=\n",
                encoding="utf-8",
            )

            changed = console_deployment.ensure_local_configuration(
                env_file,
                version="0.3.4",
                revision="a" * 40,
                deployment_id_factory=lambda: DEPLOYMENT_ID,
            )
            first = env_file.read_text(encoding="utf-8")
            refreshed = console_deployment.ensure_local_configuration(
                env_file,
                version="0.3.5",
                revision="b" * 40,
                deployment_id_factory=lambda: self.fail("identity rotated"),
            )
            second = env_file.read_text(encoding="utf-8")

            self.assertEqual(
                changed,
                frozenset(
                    {
                        "APDL_DEPLOYMENT_ID",
                        "APDL_BACKEND_VERSION",
                        "APDL_BUILD_REVISION",
                    }
                ),
            )
            self.assertEqual(
                refreshed,
                frozenset({"APDL_BACKEND_VERSION", "APDL_BUILD_REVISION"}),
            )
            self.assertIn(f"APDL_DEPLOYMENT_ID={DEPLOYMENT_ID}\n", first)
            self.assertIn(f"APDL_DEPLOYMENT_ID={DEPLOYMENT_ID}\n", second)
            self.assertIn("APDL_BACKEND_VERSION=0.3.5\n", second)
            self.assertIn(f"APDL_BUILD_REVISION={'b' * 40}\n", second)
            self.assertEqual(env_file.stat().st_mode & 0o777, 0o600)

    def test_ensure_is_idempotent_for_one_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            source = (
                f"APDL_DEPLOYMENT_ID={DEPLOYMENT_ID}\n"
                "APDL_DISPLAY_NAME=Developer APDL\n"
                "APDL_BACKEND_VERSION=0.3.4\n"
                f"APDL_BUILD_REVISION={'a' * 40}\n"
            )
            env_file.write_text(source, encoding="utf-8")

            changed = console_deployment.ensure_local_configuration(
                env_file,
                version="0.3.4",
                revision="a" * 40,
                deployment_id_factory=lambda: self.fail("identity rotated"),
            )

            self.assertEqual(changed, frozenset())
            self.assertEqual(env_file.read_text(encoding="utf-8"), source)

    def test_ensure_rejects_duplicate_or_noncanonical_identity_without_mutation(
        self,
    ) -> None:
        sources = (
            "APDL_DEPLOYMENT_ID=\nAPDL_DEPLOYMENT_ID=\n",
            "APDL_DEPLOYMENT_ID=87FAB7D6-DBA0-4F77-8FFD-00E815FC7303\n",
        )
        for source in sources:
            with (
                self.subTest(source=source),
                tempfile.TemporaryDirectory() as directory,
            ):
                env_file = Path(directory) / ".env"
                env_file.write_text(source, encoding="utf-8")

                with self.assertRaises(console_deployment.DeploymentConfigurationError):
                    console_deployment.ensure_local_configuration(
                        env_file,
                        version="0.3.4",
                        revision="a" * 40,
                    )

                self.assertEqual(env_file.read_text(encoding="utf-8"), source)

    def test_release_version_and_revision_come_from_canonical_sources(self) -> None:
        manifest = json.loads((ROOT / "release-manifest.json").read_text())
        expected_revision = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--verify", "HEAD^{commit}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        self.assertEqual(
            console_deployment.release_version(ROOT / "release-manifest.json"),
            manifest["version"],
        )
        self.assertEqual(
            console_deployment.repository_revision(ROOT),
            expected_revision,
        )
        self.assertRegex(expected_revision, r"^[0-9a-f]{40}$")

    def test_local_bootstrap_has_no_shared_deployment_identity(self) -> None:
        environment = (ROOT / ".env.example").read_text(encoding="utf-8")
        dev = (ROOT / "scripts/dev.sh").read_text(encoding="utf-8")

        self.assertIn("APDL_DEPLOYMENT_ID=\n", environment)
        self.assertIn("APDL_BACKEND_VERSION=\n", environment)
        self.assertIn("APDL_BUILD_REVISION=\n", environment)
        self.assertNotIn(DEPLOYMENT_ID, environment)
        self.assertIn(
            'python3 "$ROOT_DIR/scripts/console_deployment.py" ensure "$ROOT_DIR/.env"',
            dev,
        )


if __name__ == "__main__":
    unittest.main()
