#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "🔧 Qt Interactive Feedback MCP - Setup"
echo "========================================"
echo ""

# 1. Check uv
if ! command -v uv &>/dev/null; then
    echo "📦 uv not found. Installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# 2. Install Python dependencies
echo "📦 Installing Python dependencies..."
cd "$SCRIPT_DIR"
uv sync
echo "✅ Dependencies installed"
echo ""

# 3. Detect and configure input method (Linux only)
if [[ "$(uname)" == "Linux" ]]; then
    echo "🇨🇳 Detecting input method framework..."
    IM_MODULE="${QT_IM_MODULE:-$(im-config -m 2>/dev/null | head -1 || echo "")}"

    PLUGINS_DIR=$(uv run python -c "from PySide6.QtCore import QLibraryInfo; print(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))" 2>/dev/null)
    PIPC_DIR="$PLUGINS_DIR/platforminputcontexts"

    if [[ "$IM_MODULE" == "fcitx" ]] || pgrep -x fcitx &>/dev/null; then
        echo "   Detected: fcitx4"

        # Check if fcitx4 Qt6 plugin exists
        FCITX4_PLUGIN="$PIPC_DIR/libfcitxplatforminputcontextplugin-qt6.so"
        if [[ ! -f "$FCITX4_PLUGIN" ]]; then
            echo "   Downloading fcitx4 Qt6 plugin from Ubuntu 24.04..."
            TMP_DEB="/tmp/fcitx-frontend-qt6.deb"
            TMP_DIR="/tmp/fcitx-qt6-extract"
            wget -q "http://archive.ubuntu.com/ubuntu/pool/universe/f/fcitx-qt5/fcitx-frontend-qt6_1.2.7-2build13_amd64.deb" -O "$TMP_DEB"
            mkdir -p "$TMP_DIR"
            dpkg-deb -x "$TMP_DEB" "$TMP_DIR"
            cp "$TMP_DIR/usr/lib/x86_64-linux-gnu/qt6/plugins/platforminputcontexts/libfcitxplatforminputcontextplugin-qt6.so" "$FCITX4_PLUGIN"
            rm -rf "$TMP_DEB" "$TMP_DIR"
            echo "   ✅ fcitx4 Qt6 plugin installed"
        else
            echo "   ✅ fcitx4 Qt6 plugin already exists"
        fi

        # Update feedback_ui.py to use fcitx-remote
        if grep -q "fcitx5-remote" "$SCRIPT_DIR/feedback_ui.py" 2>/dev/null; then
            sed -i 's/fcitx5-remote/fcitx-remote/g' "$SCRIPT_DIR/feedback_ui.py"
            echo "   ✅ Updated fcitx-remote command"
        fi

    elif [[ "$IM_MODULE" == "fcitx5" ]] || pgrep -x fcitx5 &>/dev/null; then
        echo "   Detected: fcitx5"

        # Check if fcitx5 Qt6 plugin exists in PySide6
        FCITX5_PLUGIN="$PIPC_DIR/libfcitx5platforminputcontextplugin.so"
        if [[ ! -f "$FCITX5_PLUGIN" ]]; then
            # Try to copy from system
            SYS_PLUGIN="/usr/lib/x86_64-linux-gnu/qt6/plugins/platforminputcontexts/libfcitx5platforminputcontextplugin.so"
            if [[ -f "$SYS_PLUGIN" ]]; then
                cp "$SYS_PLUGIN" "$FCITX5_PLUGIN"
                echo "   ✅ fcitx5 Qt6 plugin copied from system"
            else
                echo "   ⚠️  fcitx5 Qt6 plugin not found. Install: sudo apt install fcitx5-frontend-qt6"
            fi
        else
            echo "   ✅ fcitx5 Qt6 plugin already exists"
        fi

        # Update feedback_ui.py to use fcitx5-remote
        if grep -q '"fcitx-remote"' "$SCRIPT_DIR/feedback_ui.py" 2>/dev/null; then
            sed -i 's/"fcitx-remote"/"fcitx5-remote"/g' "$SCRIPT_DIR/feedback_ui.py"
            echo "   ✅ Updated fcitx5-remote command"
        fi

    elif [[ "$IM_MODULE" == "ibus" ]] || pgrep -x ibus-daemon &>/dev/null; then
        echo "   Detected: ibus"
        echo "   ✅ ibus support is built-in, no extra configuration needed"
    else
        echo "   ⚠️  No input method framework detected"
        echo "   Chinese input may not work in Qt windows"
    fi
    echo ""
fi

# 4. Auto-configure Cursor MCP (if in a Cursor project)
echo "⚙️  Configuring Cursor MCP..."

# Find project root (look for .git directory going up)
find_project_root() {
    local dir="$1"
    while [[ "$dir" != "/" ]]; do
        if [[ -d "$dir/.git" ]] || [[ -d "$dir/.cursor" ]]; then
            echo "$dir"
            return
        fi
        dir="$(dirname "$dir")"
    done
    echo ""
}

# Try current working directory first, then script directory
PROJECT_ROOT=$(find_project_root "$(pwd)")
if [[ -z "$PROJECT_ROOT" ]]; then
    PROJECT_ROOT=$(find_project_root "$SCRIPT_DIR")
fi

if [[ -n "$PROJECT_ROOT" && "$PROJECT_ROOT" != "$SCRIPT_DIR" ]]; then
    MCP_JSON="$PROJECT_ROOT/.cursor/mcp.json"
    mkdir -p "$(dirname "$MCP_JSON")"

    if [[ -f "$MCP_JSON" ]]; then
        # Check if already configured
        if grep -q "interactive-feedback" "$MCP_JSON" 2>/dev/null; then
            echo "   ✅ MCP already configured in $MCP_JSON"
        else
            echo "   ⚠️  MCP config exists but doesn't include interactive-feedback"
            echo "   Please manually add the configuration below to $MCP_JSON"
        fi
    else
        cat > "$MCP_JSON" <<MCPJSON
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
        echo "   ✅ MCP configured at $MCP_JSON"
    fi

    # Copy rules
    RULES_DIR="$PROJECT_ROOT/.cursor/rules"
    mkdir -p "$RULES_DIR"
    if [[ ! -f "$RULES_DIR/mcp-feedback.mdc" ]]; then
        cp "$SCRIPT_DIR/.cursor/rules/mcp-feedback.mdc" "$RULES_DIR/"
        echo "   ✅ Rules copied to $RULES_DIR/mcp-feedback.mdc"
    else
        echo "   ✅ Rules already exist at $RULES_DIR/mcp-feedback.mdc"
    fi
else
    echo "   ℹ️  No project root detected. Manual configuration needed."
    echo ""
    echo "   Add to your project's .cursor/mcp.json:"
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
    echo "   Copy rules to your project:"
    echo "   cp -r $SCRIPT_DIR/.cursor/rules/ /your/project/.cursor/rules/"
fi

echo ""
echo "🎉 Setup complete! Restart Cursor to activate the MCP server."
