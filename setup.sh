#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IM_CONFIG_FILE="$SCRIPT_DIR/.im_config.json"
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

# ── Helper: check if a .so plugin's GLIBCXX requirement exceeds system libstdc++ ──
check_glibcxx_compat() {
    local plugin_path="$1"
    local im_module="$2"

    if ! command -v strings &>/dev/null; then
        echo "   ⚠️  'strings' command not found (install binutils), skipping GLIBCXX check"
        return 0
    fi

    local max_required
    max_required=$(strings "$plugin_path" 2>/dev/null | grep -oP 'GLIBCXX_3\.4\.\d+' | sort -t. -k3 -n | tail -1)
    if [[ -z "$max_required" ]]; then
        return 0
    fi

    local sys_libstdcpp="/usr/lib/x86_64-linux-gnu/libstdc++.so.6"
    if [[ ! -f "$sys_libstdcpp" ]]; then
        sys_libstdcpp=$(ldconfig -p 2>/dev/null | grep 'libstdc++.so.6 ' | head -1 | awk '{print $NF}')
    fi

    if [[ -n "$sys_libstdcpp" ]] && strings "$sys_libstdcpp" 2>/dev/null | grep -q "$max_required"; then
        echo "   ✅ System libstdc++ provides $max_required — no LD_PRELOAD needed"
        # Write config without ld_preload
        cat > "$IM_CONFIG_FILE" <<IMCFG
{
    "im_module": "$im_module",
    "ld_preload": ""
}
IMCFG
        return 0
    fi

    echo "   ⚠️  System libstdc++ lacks $max_required (needed by fcitx plugin)"
    echo "   Searching for a compatible libstdc++ ..."

    local search_paths=()
    # Conda environments
    [[ -n "$CONDA_PREFIX" && -f "$CONDA_PREFIX/lib/libstdc++.so.6" ]] && search_paths+=("$CONDA_PREFIX/lib/libstdc++.so.6")
    for d in "$HOME/miniconda3" "$HOME/miniforge3" "$HOME/anaconda3" "$HOME/mambaforge" "/opt/conda"; do
        [[ -f "$d/lib/libstdc++.so.6" ]] && search_paths+=("$d/lib/libstdc++.so.6")
    done
    # GCC toolchain installations
    for gcc_lib in /usr/lib/gcc/x86_64-linux-gnu/*/libstdc++.so; do
        [[ -f "$gcc_lib" ]] && search_paths+=("$gcc_lib")
    done
    # Snap / Flatpak / common lib paths
    for extra in /snap/core*/current/usr/lib/x86_64-linux-gnu/libstdc++.so.6 \
                 /usr/local/lib/x86_64-linux-gnu/libstdc++.so.6 \
                 /usr/lib/x86_64-linux-gnu/libstdc++.so.6.*; do
        [[ -f "$extra" ]] && search_paths+=("$extra")
    done

    for candidate in "${search_paths[@]}"; do
        local real_path
        real_path=$(readlink -f "$candidate" 2>/dev/null || echo "$candidate")
        if [[ -f "$real_path" ]] && strings "$real_path" 2>/dev/null | grep -q "$max_required"; then
            echo "   ✅ Found compatible libstdc++ at: $real_path"
            cat > "$IM_CONFIG_FILE" <<IMCFG
{
    "im_module": "$im_module",
    "ld_preload": "$real_path"
}
IMCFG
            echo "   ✅ Wrote $IM_CONFIG_FILE (LD_PRELOAD will be applied automatically)"
            return 0
        fi
    done

    echo "   ❌ No compatible libstdc++ found on this system!"
    echo "   Chinese input via fcitx may not work."
    echo ""
    echo "   To fix, install a newer libstdc++ via one of:"
    echo "     • Conda:  conda install -c conda-forge libstdcxx-ng"
    echo "     • GCC:    sudo apt install g++-11  (or newer)"
    echo "   Then re-run:  bash setup.sh"
    # Write config without ld_preload so the detected im_module is still used
    cat > "$IM_CONFIG_FILE" <<IMCFG
{
    "im_module": "$im_module",
    "ld_preload": ""
}
IMCFG
    return 1
}

# 3. Detect and configure input method (Linux only)
if [[ "$(uname)" == "Linux" ]]; then
    echo "🇨🇳 Detecting input method framework..."
    IM_MODULE="${QT_IM_MODULE:-$(im-config -m 2>/dev/null | head -1 || echo "")}"

    PLUGINS_DIR=$(uv run python -c "from PySide6.QtCore import QLibraryInfo; print(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))" 2>/dev/null)
    PIPC_DIR="$PLUGINS_DIR/platforminputcontexts"

    if [[ "$IM_MODULE" == "fcitx" ]] || pgrep -x fcitx &>/dev/null; then
        echo "   Detected: fcitx4"
        DETECTED_IM="fcitx"

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

        # Check GLIBCXX compatibility and write .im_config.json
        check_glibcxx_compat "$FCITX4_PLUGIN" "$DETECTED_IM"

        # Update feedback_ui.py to use fcitx-remote
        if grep -q "fcitx5-remote" "$SCRIPT_DIR/feedback_ui.py" 2>/dev/null; then
            sed -i 's/fcitx5-remote/fcitx-remote/g' "$SCRIPT_DIR/feedback_ui.py"
            echo "   ✅ Updated fcitx-remote command"
        fi

    elif [[ "$IM_MODULE" == "fcitx5" ]] || pgrep -x fcitx5 &>/dev/null; then
        echo "   Detected: fcitx5"
        DETECTED_IM="fcitx5"

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

        # Check GLIBCXX compatibility for fcitx5 plugin too
        if [[ -f "$FCITX5_PLUGIN" ]]; then
            check_glibcxx_compat "$FCITX5_PLUGIN" "$DETECTED_IM"
        else
            cat > "$IM_CONFIG_FILE" <<IMCFG
{
    "im_module": "$DETECTED_IM",
    "ld_preload": ""
}
IMCFG
        fi

        # Update feedback_ui.py to use fcitx5-remote
        if grep -q '"fcitx-remote"' "$SCRIPT_DIR/feedback_ui.py" 2>/dev/null; then
            sed -i 's/"fcitx-remote"/"fcitx5-remote"/g' "$SCRIPT_DIR/feedback_ui.py"
            echo "   ✅ Updated fcitx5-remote command"
        fi

    elif [[ "$IM_MODULE" == "ibus" ]] || pgrep -x ibus-daemon &>/dev/null; then
        echo "   Detected: ibus"
        echo "   ✅ ibus support is built-in, no extra configuration needed"
        cat > "$IM_CONFIG_FILE" <<IMCFG
{
    "im_module": "ibus",
    "ld_preload": ""
}
IMCFG
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
