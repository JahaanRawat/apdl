"""Static deployment contracts for the one-origin gateway boundary."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / "infra" / "docker" / "docker-compose.yml").read_text(encoding="utf-8")


def _service_block(name: str) -> str:
    marker = f"  {name}:\n"
    start = COMPOSE.index(marker)
    remainder = COMPOSE[start + len(marker) :]
    end_candidates = [
        offset
        for candidate in remainder.splitlines(keepends=True)
        if candidate.startswith("  ") and not candidate.startswith("    ")
        for offset in [remainder.index(candidate)]
    ]
    end = min(end_candidates) if end_candidates else len(remainder)
    return remainder[:end]


class UnifiedGatewayDeploymentTests(unittest.TestCase):
    def test_only_gateway_publishes_a_product_service_port(self) -> None:
        for service, port in {
            "ingestion": "8080",
            "config": "8081",
            "query": "8082",
            "agents": "8083",
            "admin-api": "8085",
        }.items():
            block = _service_block(service)
            with self.subTest(service=service):
                self.assertNotIn("    ports:\n", block)
                self.assertIn("    expose:\n", block)
                self.assertIn(f'      - "{port}"', block)

        gateway = _service_block("gateway")
        self.assertIn("    ports:\n", gateway)
        self.assertIn(
            '      - "127.0.0.1:${APDL_GATEWAY_HOST_PORT:-8000}:8000"',
            gateway,
        )
        self.assertNotIn("APDL_BIND_ADDRESS", gateway)

    def test_gateway_is_built_and_released_as_the_python_service(self) -> None:
        gateway = _service_block("gateway")
        self.assertIn("      context: ../../services/gateway", gateway)
        self.assertIn("      ADMIN_API_URL: http://admin-api:8085", gateway)
        self.assertNotIn("nginx", gateway.lower())

        manifest = json.loads((ROOT / "release-manifest.json").read_text())
        published = {image["name"]: image for image in manifest["docker_images"]}
        self.assertEqual(
            published["gateway"],
            {
                "name": "gateway",
                "repository": "ghcr.io/kuvera-apdl/apdl-gateway",
                "context": "services/gateway",
                "dockerfile": "services/gateway/Dockerfile",
                "build_args": [],
            },
        )

        dockerfile = (ROOT / "services" / "gateway" / "Dockerfile").read_text()
        self.assertIn('"--no-proxy-headers"', dockerfile)
        self.assertIn('"--no-access-log"', dockerfile)

    def test_internal_host_ports_exist_only_in_the_smoke_overlay(self) -> None:
        overlay = (
            ROOT / "scripts" / "fixtures" / "docker-compose.smoke-host-ports.yml"
        ).read_text()
        for service in ("ingestion", "config", "query", "agents", "admin-api"):
            self.assertIn(f"  {service}:\n", overlay)
        self.assertNotIn("  gateway:\n", overlay)
        self.assertNotIn("0.0.0.0", overlay)


if __name__ == "__main__":
    unittest.main()
