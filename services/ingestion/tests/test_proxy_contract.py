from pathlib import Path


def test_public_gateway_overwrites_client_forwarding_headers():
    repo_root = Path(__file__).resolve().parents[3]
    gateway = (repo_root / "services/gateway/app/main.py").read_text()
    identity = (repo_root / "services/gateway/app/client_identity.py").read_text()
    denylist = gateway.split("_REQUEST_HEADER_DENYLIST =", 1)[1].split(
        "_RESPONSE_HEADER_DENYLIST",
        1,
    )[0]
    upstream_headers = gateway.split("def _upstream_headers(", 1)[1].split(
        "\n\ndef ",
        1,
    )[0]

    assert '"forwarded"' in denylist
    assert '"x-forwarded-for"' in denylist
    assert '"x-real-ip"' in denylist
    assert (
        upstream_headers.count('headers.append(("X-Forwarded-For", client_ip))')
        == 1
    )
    assert (
        "client_ip = resolve_client_ip(request, settings.trusted_proxy_cidrs)"
        in gateway
    )
    assert "len(forwarded_values) == 1" in identity
    assert "peer_is_trusted" in identity


def test_ingestion_runtime_leaves_socket_peer_authority_to_application_policy():
    repo_root = Path(__file__).resolve().parents[3]
    dockerfile = (repo_root / "services/ingestion/Dockerfile").read_text()
    makefile = (repo_root / "Makefile").read_text()

    assert '"--no-proxy-headers"' in dockerfile
    run_ingestion = makefile.split("run-ingestion:", 1)[1].split("\n\n", 1)[0]
    assert "--no-proxy-headers" in run_ingestion
