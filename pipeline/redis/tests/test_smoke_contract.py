from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def _smoke_function() -> str:
    script = (REPO_ROOT / "scripts" / "dev.sh").read_text()
    return script.split("cmd_smoke() {", 1)[1].split("\n}\n", 1)[0]


def test_dev_smoke_delegates_once_to_the_canonical_exact_count_probe():
    smoke = _smoke_function()
    core = (REPO_ROOT / "scripts" / "smoke_core.py").read_text()
    send_once = core.split("def _send_event_once", 1)[1].split("\n\ndef ", 1)[0]

    assert smoke.count("dc_full exec -T") == 1
    assert smoke.count("gateway python -") == 1
    assert smoke.count('< "$ROOT_DIR/scripts/smoke_core.py"') == 1
    for private_origin in (
        "http://ingestion:8080",
        "http://config:8081",
        "http://query:8082",
        "http://admin-api:8085",
    ):
        assert private_origin in smoke
    assert "http_code" not in smoke
    assert "_send_event_once(args, state)" in core
    assert "_poll_exact_count(args, project_id, state)" in core
    assert "Query observed duplicate smoke events" in core
    assert send_once.count("_request_json(") == 1
    assert "while " not in send_once
    assert "for " not in send_once
