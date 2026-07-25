#!/usr/bin/env python3
"""Exact-engine proof that PostgreSQL fence loss rolls back in-flight DDL."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path


MIGRATOR_PATH = Path("/usr/local/bin/apdl-postgres-migrate")
OWNER_PID_MARKER = "__APDL_TEST_FENCE_OWNER_PID__"
PROBE_TABLE = "apdl_fence_owner_loss_probe"


def _load_migrator():
    loader = SourceFileLoader("apdl_postgres_migrate", str(MIGRATOR_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load migrator from {MIGRATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _psql(sql: str, *, capture: bool = False) -> str:
    command = ["psql", "-X", "-v", "ON_ERROR_STOP=1"]
    if capture:
        command.extend(["-A", "-t"])
    result = subprocess.run(
        command,
        input=sql,
        text=True,
        check=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
    )
    return result.stdout.strip() if capture else ""


def _owner_backend_pid(migrator, fence) -> int:
    owner = fence.process
    assert owner.stdin is not None
    owner.stdin.write(f"SELECT pg_backend_pid();\n\\echo {OWNER_PID_MARKER}\n")
    owner.stdin.flush()
    output = migrator._read_fence_response(
        owner,
        OWNER_PID_MARKER,
        timeout_seconds=migrator.MAINTENANCE_HEALTH_TIMEOUT_SECONDS,
    )
    values = [line for line in output if line]
    if len(values) != 1 or not values[0].isdigit():
        raise RuntimeError(f"Unexpected fence-owner PID response: {values!r}")
    return int(values[0])


def main() -> None:
    migrator = _load_migrator()
    _psql(
        f"""
        DROP TABLE IF EXISTS public.{PROBE_TABLE};
        CREATE TABLE public.{PROBE_TABLE} (
            id INTEGER PRIMARY KEY
        );
        """
    )
    killer_error: list[BaseException] = []
    try:
        with migrator._maintenance_fence() as fence:
            owner_pid = _owner_backend_pid(migrator, fence)

            def terminate_owner() -> None:
                try:
                    time.sleep(1)
                    terminated = _psql(
                        f"SELECT pg_terminate_backend({owner_pid});",
                        capture=True,
                    )
                    if terminated != "t":
                        raise RuntimeError(
                            f"Fence backend {owner_pid} was not terminated: "
                            f"{terminated!r}"
                        )
                except BaseException as exc:
                    killer_error.append(exc)

            killer = threading.Thread(
                target=terminate_owner,
                name="apdl-real-fence-owner-killer",
            )
            killer.start()
            try:
                migrator._psql(
                    f"""
                    BEGIN;
                    INSERT INTO public.{PROBE_TABLE} (id) VALUES (1);
                    SELECT pg_sleep(30);
                    COMMIT;
                    """,
                    fence,
                )
            except migrator.MigrationError as exc:
                if "fence ownership was lost" not in str(exc):
                    raise RuntimeError(
                        f"Unexpected owner-loss failure: {exc}"
                    ) from exc
            else:
                raise RuntimeError(
                    "Migration operation succeeded after a real fence owner was lost"
                )
            finally:
                killer.join(timeout=10)
                if killer.is_alive():
                    raise RuntimeError("Fence-owner killer did not finish")
            if killer_error:
                raise killer_error[0]

        count = _psql(
            f"SELECT count(*) FROM public.{PROBE_TABLE};",
            capture=True,
        )
        if count != "0":
            raise RuntimeError(
                "In-flight migration transaction persisted after fence-owner loss"
            )
    finally:
        _psql(f"DROP TABLE IF EXISTS public.{PROBE_TABLE};")

    print("Real PostgreSQL fence-owner loss rolled back the in-flight operation")


if __name__ == "__main__":
    main()
