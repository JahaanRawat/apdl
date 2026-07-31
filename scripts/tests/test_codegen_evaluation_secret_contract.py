"""Static security contracts for Codegen's sealed evaluation launcher."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
EVALUATION_SCRIPT = ROOT / "scripts" / "evaluate-codegen.sh"


class CodegenEvaluationSecretContractTests(unittest.TestCase):
    def test_provider_secrets_are_exported_and_forwarded_by_name_only(self) -> None:
        script = EVALUATION_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('target_name="${provider}_API_KEY"', script)
        self.assertIn('printf -v "$target_name" \'%s\' "${!source_name}"', script)
        self.assertIn('export "$target_name"', script)
        self.assertIn('docker_args+=(--env "$target_name")', script)
        self.assertNotIn("evaluation_provider_env", script)

        indirect_secret_uses = [
            line.strip()
            for line in script.splitlines()
            if "!source_name" in line
        ]
        self.assertEqual(
            indirect_secret_uses,
            [
                'if [[ -n "${!source_name:-}" ]]; then',
                'printf -v "$target_name" \'%s\' "${!source_name}"',
            ],
        )
        self.assertIn(
            'docker "${docker_args[@]}" "$controller_image_id" \\',
            script,
        )


if __name__ == "__main__":
    unittest.main()
