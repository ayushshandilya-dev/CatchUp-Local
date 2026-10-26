"""Command-line entry point."""

from __future__ import annotations

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CatchUp Local on this machine.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (keep it loopback).")
    parser.add_argument("--port", type=int, default=8000, help="Port for the web UI.")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("CatchUp Local is designed to run on loopback only.")
    os.environ.setdefault("CATCHUP_LOCAL_APP", "1")
    uvicorn.run("catchup_local.web.app:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
