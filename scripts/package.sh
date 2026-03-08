#!/bin/bash
set -e

# Reusable build and package script for Transit Tracker TUI

echo "🚀 Starting build process..."

# 1. Ensure dependencies are in sync
echo "📦 Syncing dependencies..."
uv sync --all-extras --dev

# 2. Run all tests, including capture validation
echo "🧪 Running tests..."
uv run pytest -v

# 3. Build the package artifacts
echo "🏗️ Building package artifacts..."
uv build

echo "✅ Build complete!"
echo "📦 Artifacts created in dist/:"
ls -l dist/

echo ""
echo "💡 To test the package locally using uvx:"
echo "uvx --from ./dist/*.whl transit-tracker"
