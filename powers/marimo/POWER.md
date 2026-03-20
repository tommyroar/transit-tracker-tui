---
name: "marimo"
displayName: "Marimo Notebooks"
description: "Interactive reactive Python notebooks with built-in MCP server. Create, edit, and execute notebook cells in real time for data exploration and visualization."
keywords: ["marimo", "notebook", "python", "reactive", "data-exploration"]
author: "Tommy Doerr"
---

# Marimo Notebooks

## Overview

Marimo is a modern reactive Python notebook environment. Unlike Jupyter, marimo notebooks are stored as plain `.py` files, making them git-friendly and reproducible. Cells automatically re-execute when their dependencies change.

This power connects to a running marimo editor via its built-in MCP server, allowing you to create notebooks, insert and execute cells, read outputs (including images and plots), and manage notebook sessions — all from within Kiro.

## Onboarding

### Prerequisites

- Python 3.10+ (this project uses 3.14)
- `marimo[mcp]` installed (already added to this project's dev dependencies)

### Starting the Marimo Server

Start marimo with MCP support enabled:

```bash
uv run marimo edit --mcp --no-token --port 2718
```

This launches the marimo editor at `http://localhost:2718` with the MCP endpoint at `http://localhost:2718/mcp/server`.

Flags:
- `--mcp` enables the MCP server endpoint
- `--no-token` disables auth (local dev only)
- `--port 2718` uses a fixed port so the MCP config stays stable

### Kiro MCP Configuration

The MCP server connects via HTTP. Add this to your `~/.kiro/settings/mcp.json`:

```json
{
  "mcpServers": {
    "marimo": {
      "url": "http://localhost:2718/mcp/server"
    }
  }
}
```

Or install this power via the Kiro Powers UI → "Add Custom Power" → "Local Directory" and point it to this power's folder.

### Verification

1. Start marimo: `uv run marimo edit --mcp --no-token --port 2718`
2. Open a notebook in the marimo UI (or create a new one)
3. In Kiro, verify the MCP server shows as connected in the MCP panel

## Common Workflows

### Workflow 1: Explore GTFS Data

Create a notebook to query the transit tracker's SQLite database:

1. Start marimo and create a new notebook
2. Use the MCP tools to insert cells that import sqlite3 and query `data/gtfs_index.sqlite`
3. Execute cells to see results inline

Example cell content:
```python
import sqlite3
import marimo as mo

conn = sqlite3.connect("data/gtfs_index.sqlite")
cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
mo.md(f"Tables: {', '.join(t[0] for t in tables)}")
```

### Workflow 2: Prototype Visualizations

Use marimo's reactive cells to build interactive charts:

1. Insert a cell with data loading logic
2. Insert a cell with plotting code (matplotlib, plotly, altair)
3. Cells re-execute automatically when dependencies change

### Workflow 3: Quick Script Prototyping

Before writing a full script in `scripts/`, prototype the logic in a marimo notebook:

1. Create a notebook in the project root or a `notebooks/` folder
2. Iterate on the logic with instant feedback
3. Once working, extract to a standalone script

## MCP Tools

The marimo MCP server exposes AI tools for interacting with notebooks. Available tools are loaded dynamically when the server connects. Common capabilities include:

- Reading notebook content and cell outputs
- Inserting, editing, and deleting cells
- Executing cells and retrieving results
- Managing notebook sessions

Use the `active_notebooks` prompt to discover running sessions and their IDs.

## Troubleshooting

### MCP Server Won't Connect

**Symptoms:** Kiro shows the marimo MCP server as disconnected

**Solutions:**
1. Ensure marimo is running: `uv run marimo edit --mcp --no-token --port 2718`
2. Verify the port matches your config (default: 2718)
3. Check that `--mcp` flag is included in the start command
4. Try opening `http://localhost:2718/mcp/server` in a browser to confirm the endpoint is live

### "Module not found" Errors in Cells

**Cause:** The marimo server runs in its own environment

**Solution:** Install packages into the project venv that marimo uses:
```bash
uv add <package-name>
```

### Notebook Not Appearing

**Cause:** No notebook is open in the marimo editor

**Solution:** Open or create a notebook in the marimo UI at `http://localhost:2718` before using MCP tools.

## Best Practices

- Use `--port 2718` consistently so the MCP URL stays stable across sessions
- Keep notebooks in a `notebooks/` directory to separate them from source code
- Marimo notebooks are plain `.py` files — commit them to git like any other code
- Use `mo.md()` for rich output and `mo.ui` for interactive widgets
- For heavy data work, add packages like `pandas`, `polars`, or `altair` to dev dependencies
