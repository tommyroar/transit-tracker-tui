"""Minimal Docker entrypoint — runs the WebSocket server directly.

Bypasses cli.py which tries to launch the macOS GUI subprocess.
"""

import asyncio

from transit_tracker.config import TransitConfig
from transit_tracker.network.websocket_server import run_server


def main():
    config = TransitConfig.load("/config/config.yaml")
    asyncio.run(run_server(host="0.0.0.0", port=8000, config=config))


if __name__ == "__main__":
    main()
