import sys

import rumps

try:
    print("Testing rumps GUI context...")
    app = rumps.App("TestApp", title="🏙️")
    print("App created successfully.")
    # We won't call app.run() as it blocks, we just want to see if it can init
except Exception as e:
    print(f"FAILED: {e}", file=sys.stderr)
    sys.exit(1)
