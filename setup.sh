#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "🔧 Qt Interactive Feedback MCP - Setup"
echo "========================================"
echo ""

# 1. Install dependencies
echo "📦 Installing Python dependencies..."
cd "$SCRIPT_DIR"
if command -v uv &>/dev/null; then
    uv sync
else
    echo "❌ uv not found. Install it first: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi
echo "✅ Dependencies installed"
echo ""

# 2. Setup fcitx4 Qt6 plugin (Linux only)
if [[ "$(uname)" == "Linux" ]]; then
    echo "🇨🇳 Setting up fcitx4 Qt6 input method plugin..."
    PLUGINS_DIR=$(uv run python -c "from PySide6.QtCore import QLibraryInfo; print(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))" 2>/dev/null)
    TARGET="$PLUGINS_DIR/platforminputcontexts/libfcitxplatforminputcontextplugin-qt6.so"

    if [[ -f "$TARGET" ]]; then
        echo "✅ fcitx4 Qt6 plugin already exists"
    else
        echo "   Downloading fcitx-frontend-qt6 from Ubuntu 24.04..."
        TMP_DEB="/tmp/fcitx-frontend-qt6.deb"
        TMP_DIR="/tmp/fcitx-qt6-extract"
        wget -q "http://archive.ubuntu.com/ubuntu/pool/universe/f/fcitx-qt5/fcitx-frontend-qt6_1.2.7-2build13_amd64.deb" -O "$TMP_DEB"
        mkdir -p "$TMP_DIR"
        dpkg-deb -x "$TMP_DEB" "$TMP_DIR"
        cp "$TMP_DIR/usr/lib/x86_64-linux-gnu/qt6/plugins/platforminputcontexts/libfcitxplatforminputcontextplugin-qt6.so" "$TARGET"
        rm -rf "$TMP_DEB" "$TMP_DIR"
        echo "✅ fcitx4 Qt6 plugin installed"
    fi
    echo ""
fi

# 3. Show MCP configuration
echo "⚙️  Cursor MCP Configuration"
echo "----------------------------"
echo "Add the following to your Cursor MCP settings (.cursor/mcp.json):"
echo ""
cat <<MCPJSON
{
  "mcpServers": {
    "interactive-feedback": {
      "command": "uv",
      "args": [
        "--directory",
        "$SCRIPT_DIR",
        "run",
        "server.py"
      ],
      "timeout": 3600,
      "autoApprove": [
        "interactive_feedback"
      ]
    }
  }
}
MCPJSON
echo ""

# 4. Copy rules
echo "📋 Cursor Rules"
echo "----------------"
echo "To enable automatic feedback interaction rules, copy the rules to your project:"
echo ""
echo "  cp -r $SCRIPT_DIR/.cursor/rules/ /your/project/.cursor/rules/"
echo ""
echo "Or add them to Cursor Settings > Rules > User Rules manually."
echo ""

echo "🎉 Setup complete!"
