"""Fires a Pushcut webhook to trigger the iPhone Shortcuts automation.

Standalone — only tests the trigger half of the link (Jetson/dev machine ->
silent push -> Shortcuts automation starts). Run scripts/receive_photo.py
first in another terminal to catch the resulting photo upload.

Usage:
  python scripts/trigger_pushcut.py <webhook-url>

The webhook URL comes from the Pushcut app: Automations tab -> your
automation -> copy the "Webhook URL".
"""

from __future__ import annotations

import sys

import httpx


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)

    url = sys.argv[1]
    print(f"POST {url} ...")
    response = httpx.post(url, timeout=5.0)
    print(f"status={response.status_code} body={response.text}")


if __name__ == "__main__":
    main()
