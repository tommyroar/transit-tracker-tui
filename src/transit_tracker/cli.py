import sys
import argparse
import asyncio
from .tui import run_cli
# Service module temporarily disabled
# from .network.websocket_service import run_service

def main():
    parser = argparse.ArgumentParser(description="Transit Tracker Configuration")
    parser.add_argument(
        "command", 
        nargs="?", 
        choices=["ui"], 
        default="ui",
        help="Command to run: 'ui' (default) opens the interactive configuration wizard."
    )

    args = parser.parse_args()

    if args.command == "service":
        print("Service functionality is currently disabled.")
        sys.exit(1)
        # asyncio.run(run_service())
    else:
        run_cli()

if __name__ == "__main__":
    main()
