"""Container health probe for a gateway whose root intentionally stays 404."""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main() -> None:
    hosts = json.loads(os.environ["APDL_GATEWAY_ALLOWED_HOSTS"])
    request = Request(
        "http://127.0.0.1:8000/",
        headers={"Host": hosts[0]},
    )
    try:
        urlopen(request, timeout=2)
    except HTTPError as error:
        payload = json.load(error)
        if error.code == 404 and payload.get("code") == "route_not_found":
            return
    raise RuntimeError("gateway route guard is not healthy")


if __name__ == "__main__":
    main()
