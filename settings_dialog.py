"""Settings dialog and version checker for MCP Feedback Assistant."""
import os
import sys
import json
import subprocess
import threading
import urllib.request
import urllib.error

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QGroupBox, QFrame, QTextEdit, QSpinBox, QLineEdit,
)
from PySide6.QtCore import Qt, QSettings, Signal, QObject
from PySide6.QtGui import QPainter, QColor

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_VERSION_FILE = os.path.join(_SCRIPT_DIR, "VERSION")
_REMOTE_VERSION_URL = (
    "https://api.github.com/repos/rooney2020/qt-interactive-feedback-mcp/contents/VERSION"
)

DARK_BG = "#1e1e2e"
DARK_SURFACE = "#2a2a3e"
DARK_BORDER = "#3a3a5a"
ACCENT_BLUE = "#6cacfe"
ACCENT_GREEN = "#7ec87e"
TEXT_PRIMARY = "#e0e0e0"
TEXT_SECONDARY = "#999"


def local_version() -> str:
    try:
        with open(_VERSION_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return "unknown"


class _VersionSignal(QObject):
    result = Signal(str, str)  # (remote_version, error)


class _FeishuOAuthSignal(QObject):
    done = Signal(bool, str)  # (success, message)


def check_version_async(callback):
    """Check remote version in background thread. Calls callback(remote_ver, error) on main thread."""
    sig = _VersionSignal()
    sig.result.connect(callback)

    def _worker():
        try:
            req = urllib.request.Request(_REMOTE_VERSION_URL, method="GET")
            req.add_header("User-Agent", "MCP-Feedback-Assistant")
            req.add_header("Accept", "application/vnd.github.raw+json")
            with urllib.request.urlopen(req, timeout=15) as resp:
                remote = resp.read().decode("utf-8").strip()
            sig.result.emit(remote, "")
        except Exception as e:
            sig.result.emit("", str(e))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return sig  # caller must keep reference to prevent GC


# ── Badge Button ──────────────────────────────────────────────────────

class BadgePushButton(QPushButton):
    """QPushButton with optional red dot badge at top-right corner."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._show_badge = False

    def set_badge(self, show: bool):
        self._show_badge = show
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._show_badge:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor("#e53935"))
            painter.setPen(QColor("#ff6b6b"))
            r = max(7, self.width() // 5)
            painter.drawEllipse(self.width() - r - 1, 1, r, r)
            painter.end()


# ── Settings keys ──────────────────────────────────────────────────────

SETTINGS_ORG = "InteractiveFeedbackMCP"
SETTINGS_APP = "Settings"

KEY_CHINESE_DEFAULT = "chinese_mode_default"
KEY_REREAD_RULES_DEFAULT = "reread_rules_default"
KEY_CHECK_UPDATE = "check_update_on_start"
KEY_CUSTOM_SUFFIX = "custom_suffix_text"
KEY_TIMEOUT_MINUTES = "timeout_minutes"
KEY_AUTO_REPLY_SECONDS = "auto_reply_seconds"
KEY_HAS_UPDATE = "has_update"

DEFAULT_TIMEOUT_MINUTES = 720  # 12 hours
DEFAULT_AUTO_REPLY_SECONDS = 3000
MAX_AUTO_REPLY_SECONDS = 3500
AUTO_REPLY_MESSAGE = "请稍等, 我不在座位, 请立刻再次调用qt-interactive-feedback MCP与我交互, 刷新工具超时时间"


def load_settings() -> dict:
    s = QSettings(SETTINGS_ORG, SETTINGS_APP)
    return {
        KEY_CHINESE_DEFAULT: s.value(KEY_CHINESE_DEFAULT, True, type=bool),
        KEY_REREAD_RULES_DEFAULT: s.value(KEY_REREAD_RULES_DEFAULT, False, type=bool),
        KEY_CHECK_UPDATE: s.value(KEY_CHECK_UPDATE, True, type=bool),
        KEY_CUSTOM_SUFFIX: s.value(KEY_CUSTOM_SUFFIX, "", type=str),
        KEY_TIMEOUT_MINUTES: s.value(KEY_TIMEOUT_MINUTES, DEFAULT_TIMEOUT_MINUTES, type=int),
        KEY_AUTO_REPLY_SECONDS: s.value(KEY_AUTO_REPLY_SECONDS, DEFAULT_AUTO_REPLY_SECONDS, type=int),
    }


def save_settings(data: dict):
    s = QSettings(SETTINGS_ORG, SETTINGS_APP)
    for k, v in data.items():
        s.setValue(k, v)


def set_update_flag(has_update: bool):
    s = QSettings(SETTINGS_ORG, SETTINGS_APP)
    s.setValue(KEY_HAS_UPDATE, has_update)


def has_update_flag() -> bool:
    s = QSettings(SETTINGS_ORG, SETTINGS_APP)
    return s.value(KEY_HAS_UPDATE, False, type=bool)


def get_soft_timeout() -> int:
    """Return SOFT_TIMEOUT in seconds from saved settings. Called by server.py.
    Uses auto_reply_seconds directly since it represents the countdown before
    auto-reply to avoid Cursor's hardcoded 3600s tool-call timeout."""
    s = QSettings(SETTINGS_ORG, SETTINGS_APP)
    return s.value(KEY_AUTO_REPLY_SECONDS, DEFAULT_AUTO_REPLY_SECONDS, type=int)


def get_auto_reply_seconds() -> int:
    """Return auto-reply countdown seconds. Called by server.py and feedback_ui.py."""
    s = QSettings(SETTINGS_ORG, SETTINGS_APP)
    return s.value(KEY_AUTO_REPLY_SECONDS, DEFAULT_AUTO_REPLY_SECONDS, type=int)


def _find_mcp_json_paths() -> list:
    """Find all potential mcp.json paths (user-level and project-level)."""
    paths = []
    home = os.path.expanduser("~")
    user_mcp = os.path.join(home, ".cursor", "mcp.json")
    if os.path.isfile(user_mcp):
        paths.append(user_mcp)
    return paths


def sync_mcp_json_timeout(timeout_minutes: int) -> list:
    """Update timeout in all found mcp.json files. Returns list of (path, success, msg)."""
    timeout_sec = timeout_minutes * 60
    results = []
    for path in _find_mcp_json_paths():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            servers = data.get("mcpServers", {})
            updated = False
            for name, cfg in servers.items():
                if "interactive" in name.lower() and "feedback" in name.lower():
                    cfg["timeout"] = timeout_sec
                    updated = True
            if updated:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                results.append((path, True, f"已更新 timeout={timeout_sec}"))
            else:
                results.append((path, False, "未找到 interactive-feedback server 配置"))
        except Exception as e:
            results.append((path, False, str(e)))
    return results


# ── Settings Dialog ────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    def __init__(self, parent=None, has_update: bool = None):
        super().__init__(parent)
        self._has_update = has_update if has_update is not None else has_update_flag()
        self.setWindowTitle("MCP 反馈助手 - 设置")
        self.setMinimumWidth(420)
        _check_svg = os.path.join(_SCRIPT_DIR, "images", "check-blue.svg").replace("\\", "/")
        self.setStyleSheet(f"""
            QDialog {{ background-color: {DARK_BG}; color: {TEXT_PRIMARY}; }}
            QCheckBox {{ spacing: 10px; font-size: 13px; color: {TEXT_PRIMARY}; padding: 1px 4px; margin-left: 12px; background: transparent; border: none; }}
            QCheckBox:checked {{ background: transparent; }}
            QCheckBox::indicator {{
                width: 16px; height: 16px; border-radius: 3px;
                border: 2px solid {DARK_BORDER}; background-color: {DARK_SURFACE};
            }}
            QCheckBox::indicator:checked {{
                border-color: {ACCENT_BLUE}; background-color: {DARK_SURFACE};
                image: url("{_check_svg}");
            }}
            QLabel {{ color: {TEXT_PRIMARY}; font-size: 13px; }}
            QTextEdit {{
                background-color: {DARK_SURFACE}; border: 1px solid {DARK_BORDER};
                border-radius: 4px; padding: 6px; color: {TEXT_PRIMARY}; font-size: 12px;
            }}
            QPushButton {{
                background: {ACCENT_BLUE}; color: white; border: none;
                border-radius: 4px; padding: 6px 20px; font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover {{ background: #5ab0ff; }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Version info + update status on same row
        ver_row = QHBoxLayout()
        ver_label = QLabel(f"当前版本: {local_version()}")
        ver_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
        ver_row.addWidget(ver_label)
        self._update_label = QLabel("")
        self._update_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
        self._update_label.setVisible(False)
        ver_row.addWidget(self._update_label)
        ver_row.addStretch()
        layout.addLayout(ver_row)

        _section_style = (
            f"QFrame {{ background-color: {DARK_SURFACE}; border: 1px solid {DARK_BORDER}; "
            f"border-radius: 6px; padding: 10px; }}"
        )
        _section_title_style = (
            f"color: {ACCENT_BLUE}; font-size: 13px; font-weight: bold; "
            f"padding: 0; margin-top: 4px;"
        )

        # Default toggles
        lbl_defaults = QLabel("默认开关")
        lbl_defaults.setStyleSheet(_section_title_style)
        layout.addWidget(lbl_defaults)

        self.cb_chinese = QCheckBox("使用中文（默认勾选）")
        self.cb_reread = QCheckBox("重新读取Rules（默认勾选）")
        self.cb_update = QCheckBox("启动时检查更新")
        layout.addWidget(self.cb_chinese)
        layout.addWidget(self.cb_reread)
        layout.addWidget(self.cb_update)

        # Timeout setting
        lbl_timeout = QLabel("超时时间")
        lbl_timeout.setStyleSheet(_section_title_style)
        layout.addWidget(lbl_timeout)

        _spin_style = (
            f"QSpinBox {{ background-color: {DARK_SURFACE}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {DARK_BORDER}; border-radius: 4px; padding: 4px 8px; font-size: 13px; }}"
            f"QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}"
        )
        timeout_row = QHBoxLayout()
        timeout_row.setContentsMargins(12, 0, 0, 0)
        timeout_row.addWidget(QLabel("单次调用最大等待："))
        self.timeout_hours_spin = QSpinBox()
        self.timeout_hours_spin.setRange(0, 48)
        self.timeout_hours_spin.setSuffix(" 小时")
        self.timeout_hours_spin.setStyleSheet(_spin_style)
        timeout_row.addWidget(self.timeout_hours_spin)

        self.timeout_mins_spin = QSpinBox()
        self.timeout_mins_spin.setRange(0, 59)
        self.timeout_mins_spin.setSuffix(" 分钟")
        self.timeout_mins_spin.setStyleSheet(_spin_style)
        timeout_row.addWidget(self.timeout_mins_spin)
        timeout_row.addStretch()
        layout.addLayout(timeout_row)

        timeout_hint = QLabel("保存后自动同步 mcp.json，修改后需重启 Cursor 生效")
        timeout_hint.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px; margin-left: 12px;")
        layout.addWidget(timeout_hint)

        # Auto-reply countdown
        lbl_auto_reply = QLabel("自动回复倒计时")
        lbl_auto_reply.setStyleSheet(_section_title_style)
        layout.addWidget(lbl_auto_reply)

        auto_reply_row = QHBoxLayout()
        auto_reply_row.setContentsMargins(12, 0, 0, 0)
        auto_reply_row.addWidget(QLabel("倒计时秒数："))
        self.auto_reply_spin = QSpinBox()
        self.auto_reply_spin.setRange(60, MAX_AUTO_REPLY_SECONDS)
        self.auto_reply_spin.setSuffix(" 秒")
        self.auto_reply_spin.setStyleSheet(_spin_style)
        self.auto_reply_spin.valueChanged.connect(self._validate_auto_reply)
        auto_reply_row.addWidget(self.auto_reply_spin)
        auto_reply_row.addStretch()
        layout.addLayout(auto_reply_row)

        auto_reply_hint = QLabel(
            f"Cursor 对单个 MCP 工具调用有 3600 秒硬性超时。\n"
            f"倒计时结束前未回复，将自动发送心跳消息避免超时。\n"
            f"允许范围: 60 ~ {MAX_AUTO_REPLY_SECONDS} 秒（预留 100 秒缓冲）"
        )
        auto_reply_hint.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px; margin-left: 12px;")
        layout.addWidget(auto_reply_hint)

        self._auto_reply_warn_label = QLabel("")
        self._auto_reply_warn_label.setStyleSheet("color: #ff6b8a; font-size: 11px; margin-left: 12px;")
        self._auto_reply_warn_label.setVisible(False)
        layout.addWidget(self._auto_reply_warn_label)

        self._mcp_status_label = QLabel("")
        self._mcp_status_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
        self._mcp_status_label.setVisible(False)
        layout.addWidget(self._mcp_status_label)

        # Custom suffix
        lbl_suffix = QLabel("自定义追加文本")
        lbl_suffix.setStyleSheet(_section_title_style)
        layout.addWidget(lbl_suffix)

        hint = QLabel("每次提交反馈时自动追加的文本（留空则不追加）：")
        hint.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px; margin-left: 12px;")
        layout.addWidget(hint)
        self.suffix_edit = QTextEdit()
        self.suffix_edit.setMaximumHeight(80)
        self.suffix_edit.setPlaceholderText("例如：请使用简洁的语言回复")
        self.suffix_edit.setStyleSheet(
            self.suffix_edit.styleSheet() if self.suffix_edit.styleSheet() else
            f"QTextEdit {{ background-color: {DARK_SURFACE}; border: 1px solid {DARK_BORDER}; "
            f"border-radius: 4px; padding: 6px; color: {TEXT_PRIMARY}; font-size: 12px; margin-left: 12px; }}"
        )
        layout.addWidget(self.suffix_edit)

        # ── Feishu integration ─────────────────────────────────────────────
        lbl_feishu = QLabel("飞书集成")
        lbl_feishu.setStyleSheet(_section_title_style)
        layout.addWidget(lbl_feishu)

        feishu_hint = QLabel("连接飞书后可在反馈输入框中 @用户/@群/@部门")
        feishu_hint.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px; margin-left: 12px;")
        layout.addWidget(feishu_hint)

        _input_style = (
            f"QLineEdit {{ background-color: {DARK_SURFACE}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {DARK_BORDER}; border-radius: 4px; padding: 4px 8px; font-size: 12px; }}"
        )
        feishu_grid = QVBoxLayout()
        feishu_grid.setContentsMargins(12, 0, 0, 0)

        # App ID
        row_id = QHBoxLayout()
        row_id.addWidget(QLabel("App ID:"))
        self.feishu_app_id = QLineEdit()
        self.feishu_app_id.setPlaceholderText("自动从 mcp.json 读取（可手动覆盖）")
        self.feishu_app_id.setStyleSheet(_input_style)
        row_id.addWidget(self.feishu_app_id)
        feishu_grid.addLayout(row_id)

        # App Secret
        row_secret = QHBoxLayout()
        row_secret.addWidget(QLabel("App Secret:"))
        self.feishu_app_secret = QLineEdit()
        self.feishu_app_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.feishu_app_secret.setPlaceholderText("自动从 mcp.json 读取（可手动覆盖）")
        self.feishu_app_secret.setStyleSheet(_input_style)
        row_secret.addWidget(self.feishu_app_secret)
        feishu_grid.addLayout(row_secret)

        layout.addLayout(feishu_grid)

        # OAuth connect / status
        feishu_btn_row = QHBoxLayout()
        feishu_btn_row.setContentsMargins(12, 0, 0, 0)

        self._feishu_status = QLabel("")
        self._feishu_status.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
        feishu_btn_row.addWidget(self._feishu_status)
        feishu_btn_row.addStretch()

        self._feishu_connect_btn = QPushButton("连接飞书")
        self._feishu_connect_btn.setStyleSheet(
            f"QPushButton {{ background: {DARK_SURFACE}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {DARK_BORDER}; padding: 4px 14px; font-size: 12px; font-weight: normal; }}"
            f"QPushButton:hover {{ border-color: {ACCENT_BLUE}; }}"
        )
        self._feishu_connect_btn.clicked.connect(self._feishu_oauth)
        feishu_btn_row.addWidget(self._feishu_connect_btn)

        self._feishu_disconnect_btn = QPushButton("断开")
        self._feishu_disconnect_btn.setStyleSheet(
            f"QPushButton {{ background: {DARK_SURFACE}; color: #ff6b8a; "
            f"border: 1px solid {DARK_BORDER}; padding: 4px 14px; font-size: 12px; font-weight: normal; }}"
            f"QPushButton:hover {{ border-color: #ff6b8a; }}"
        )
        self._feishu_disconnect_btn.clicked.connect(self._feishu_disconnect)
        feishu_btn_row.addWidget(self._feishu_disconnect_btn)

        layout.addLayout(feishu_btn_row)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._check_btn_style_normal = (
            f"QPushButton {{ background: {DARK_SURFACE}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {DARK_BORDER}; }} QPushButton:hover {{ border-color: {ACCENT_BLUE}; }}"
        )
        self._check_btn_style_update = (
            f"QPushButton {{ background: #d84315; color: white; "
            f"border: none; border-radius: 4px; }} QPushButton:hover {{ background: #e65100; }}"
        )
        if self._has_update:
            self._check_btn = BadgePushButton("立即更新")
            self._check_btn.setStyleSheet(self._check_btn_style_update)
            self._check_btn.clicked.connect(self._do_update)
        else:
            self._check_btn = BadgePushButton("检查更新")
            self._check_btn.setStyleSheet(self._check_btn_style_normal)
            self._check_btn.clicked.connect(self._do_check_update)
        btn_layout.addWidget(self._check_btn)

        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._save_and_close)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

        self._load()

    def _validate_auto_reply(self, value: int):
        if value > MAX_AUTO_REPLY_SECONDS:
            self._auto_reply_warn_label.setText(
                f"⚠️ 不能超过 {MAX_AUTO_REPLY_SECONDS} 秒！\n"
                f"原因：Cursor IDE 对 MCP 工具调用有 3600 秒硬性超时限制，\n"
                f"必须预留至少 100 秒缓冲时间，否则会被 Cursor 强制中断。"
            )
            self._auto_reply_warn_label.setVisible(True)
            self.auto_reply_spin.setValue(MAX_AUTO_REPLY_SECONDS)
        elif value < 60:
            self._auto_reply_warn_label.setText("⚠️ 最少 60 秒，太短会导致频繁自动回复。")
            self._auto_reply_warn_label.setVisible(True)
            self.auto_reply_spin.setValue(60)
        else:
            self._auto_reply_warn_label.setVisible(False)

    def _load(self):
        data = load_settings()
        self.cb_chinese.setChecked(data[KEY_CHINESE_DEFAULT])
        self.cb_reread.setChecked(data[KEY_REREAD_RULES_DEFAULT])
        self.cb_update.setChecked(data[KEY_CHECK_UPDATE])
        self.suffix_edit.setPlainText(data[KEY_CUSTOM_SUFFIX])
        total_mins = data.get(KEY_TIMEOUT_MINUTES, DEFAULT_TIMEOUT_MINUTES)
        self.timeout_hours_spin.setValue(total_mins // 60)
        self.timeout_mins_spin.setValue(total_mins % 60)
        self.auto_reply_spin.setValue(data.get(KEY_AUTO_REPLY_SECONDS, DEFAULT_AUTO_REPLY_SECONDS))
        if self._has_update:
            self._check_btn.set_badge(True)

        # Feishu settings
        s = QSettings("InteractiveFeedbackMCP", "Settings")
        self.feishu_app_id.setText(s.value("feishu_app_id", "", type=str))
        self.feishu_app_secret.setText(s.value("feishu_app_secret", "", type=str))
        self._refresh_feishu_status()

    def _save_and_close(self):
        total_mins = self.timeout_hours_spin.value() * 60 + self.timeout_mins_spin.value()
        if total_mins < 1:
            total_mins = 1
        auto_reply_sec = self.auto_reply_spin.value()
        if auto_reply_sec > MAX_AUTO_REPLY_SECONDS:
            auto_reply_sec = MAX_AUTO_REPLY_SECONDS
        if auto_reply_sec < 60:
            auto_reply_sec = 60
        save_settings({
            KEY_CHINESE_DEFAULT: self.cb_chinese.isChecked(),
            KEY_REREAD_RULES_DEFAULT: self.cb_reread.isChecked(),
            KEY_CHECK_UPDATE: self.cb_update.isChecked(),
            KEY_CUSTOM_SUFFIX: self.suffix_edit.toPlainText().strip(),
            KEY_TIMEOUT_MINUTES: total_mins,
            KEY_AUTO_REPLY_SECONDS: auto_reply_sec,
        })
        # Feishu credentials
        s = QSettings("InteractiveFeedbackMCP", "Settings")
        s.setValue("feishu_app_id", self.feishu_app_id.text().strip())
        s.setValue("feishu_app_secret", self.feishu_app_secret.text().strip())
        results = sync_mcp_json_timeout(total_mins)
        if results:
            msgs = []
            for path, ok, msg in results:
                short_path = path.replace(os.path.expanduser("~"), "~")
                msgs.append(f"{'✅' if ok else '⚠️'} {short_path}: {msg}")
            self._mcp_status_label.setText("\n".join(msgs) + "\n⚠️ 修改 mcp.json 后需重启 Cursor 生效")
            self._mcp_status_label.setStyleSheet(f"color: #f0a050; font-size: 11px;")
            self._mcp_status_label.setVisible(True)
        else:
            self._mcp_status_label.setText("⚠️ 未找到 ~/.cursor/mcp.json，请手动配置 timeout")
            self._mcp_status_label.setStyleSheet(f"color: #f0a050; font-size: 11px;")
            self._mcp_status_label.setVisible(True)
            self.accept()
            return
        self.accept()

    # ── Feishu methods ──────────────────────────────────────────────────

    def _get_feishu_client(self):
        try:
            from feishu_client import FeishuClient
            client = FeishuClient()
            # If user typed custom credentials, apply them
            aid = self.feishu_app_id.text().strip()
            asec = self.feishu_app_secret.text().strip()
            if aid:
                client._app_id = aid
            if asec:
                client._app_secret = asec
            return client
        except Exception:
            return None

    def _refresh_feishu_status(self):
        client = self._get_feishu_client()
        if client is None:
            self._feishu_status.setText("飞书模块不可用")
            self._feishu_status.setStyleSheet("color: #ff6b8a; font-size: 12px;")
            return
        status = client.status_text
        if client.has_user_token:
            self._feishu_status.setText(f"✅ {status}")
            self._feishu_status.setStyleSheet(f"color: {ACCENT_GREEN}; font-size: 12px;")
            self._feishu_disconnect_btn.setVisible(True)
        elif client.is_configured:
            self._feishu_status.setText(f"✅ {status}")
            self._feishu_status.setStyleSheet(f"color: {ACCENT_GREEN}; font-size: 12px;")
            self._feishu_disconnect_btn.setVisible(bool(client._user_refresh))
        else:
            self._feishu_status.setText(status)
            self._feishu_status.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
            self._feishu_disconnect_btn.setVisible(False)

    def _feishu_oauth(self):
        s = QSettings("InteractiveFeedbackMCP", "Settings")
        s.setValue("feishu_app_id", self.feishu_app_id.text().strip())
        s.setValue("feishu_app_secret", self.feishu_app_secret.text().strip())

        client = self._get_feishu_client()
        if client is None or not client.is_configured:
            self._feishu_status.setText("❌ 请先配置 App ID 和 App Secret")
            self._feishu_status.setStyleSheet("color: #ff6b8a; font-size: 12px;")
            return

        self._feishu_status.setText("⏳ 正在等待浏览器授权...")
        self._feishu_status.setStyleSheet(f"color: {ACCENT_BLUE}; font-size: 12px;")
        self._feishu_connect_btn.setEnabled(False)

        self._oauth_sig = _FeishuOAuthSignal()
        self._oauth_sig.done.connect(self._feishu_oauth_done)

        port = s.value("feishu_oauth_port", 3000, type=int)
        sig = self._oauth_sig

        def _on_done(success, msg):
            sig.done.emit(success, msg)

        client.start_oauth(port=port, callback=_on_done)
        self._active_feishu_client = client

    def _feishu_oauth_done(self, success, msg):
        self._feishu_connect_btn.setEnabled(True)
        if success:
            self._feishu_status.setText(f"✅ {msg}")
            self._feishu_status.setStyleSheet(f"color: {ACCENT_GREEN}; font-size: 12px;")
            self._feishu_disconnect_btn.setVisible(True)
            # Reload the feishu client in the parent daemon window
            parent = self.parent()
            if parent and hasattr(parent, '_feishu_client'):
                try:
                    from feishu_client import FeishuClient
                    parent._feishu_client = FeishuClient()
                except Exception:
                    pass
        else:
            self._feishu_status.setText(f"❌ {msg}")
            self._feishu_status.setStyleSheet("color: #ff6b8a; font-size: 12px;")

    def _feishu_disconnect(self):
        client = self._get_feishu_client()
        if client:
            client.disconnect()
        self._refresh_feishu_status()
        parent = self.parent()
        if parent and hasattr(parent, '_feishu_client') and parent._feishu_client:
            parent._feishu_client.disconnect()

    def _do_check_update(self):
        self._update_label.setText("正在检查...")
        self._update_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
        self._update_label.setVisible(True)
        self._ver_sig = check_version_async(self._on_version_result)

    def _on_version_result(self, remote_ver: str, error: str):
        if error:
            self._update_label.setText(f"检查失败: {error}")
            self._update_label.setStyleSheet("color: #ff6b8a; font-size: 12px;")
        elif remote_ver == local_version():
            self._update_label.setText(f"✅ 已是最新版本 ({remote_ver})")
            self._update_label.setStyleSheet(f"color: {ACCENT_GREEN}; font-size: 12px;")
        else:
            set_update_flag(True)
            self._has_update = True
            self._update_label.setText(f"🔄 有新版本: {remote_ver}（当前: {local_version()}）")
            self._update_label.setStyleSheet(f"color: #f0a050; font-size: 12px;")
            self._check_btn.setText("立即更新")
            self._check_btn.setStyleSheet(self._check_btn_style_update)
            self._check_btn.set_badge(True)
            try:
                self._check_btn.clicked.disconnect()
            except RuntimeError:
                pass
            self._check_btn.clicked.connect(self._do_update)
        self._update_label.setVisible(True)

    def _do_update(self):
        dlg = UpdateDialog(self)
        dlg.exec()

    def _restart_daemon(self):
        python = sys.executable
        daemon_script = os.path.join(_SCRIPT_DIR, "feedback_daemon.py")
        if os.path.exists(daemon_script):
            subprocess.Popen([python, daemon_script], start_new_session=True)
        from PySide6.QtWidgets import QApplication
        QApplication.quit()


class _UpdateSignal(QObject):
    log = Signal(str)
    finished = Signal(bool, str)  # (success, summary)


class UpdateDialog(QDialog):
    """Separate dialog for update progress."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("更新 MCP 反馈助手")
        self.setMinimumSize(480, 300)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {DARK_BG}; color: {TEXT_PRIMARY}; }}
            QPushButton {{
                background: {ACCENT_BLUE}; color: white; border: none;
                border-radius: 4px; padding: 8px 24px; font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover {{ background: #5ab0ff; }}
            QPushButton:disabled {{ background: #444466; color: #888888; }}
        """)

        layout = QVBoxLayout(self)

        title = QLabel("正在更新...")
        title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {ACCENT_BLUE};")
        layout.addWidget(title)
        self._title = title

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet(
            f"QTextEdit {{ background-color: #0d0d18; border: 1px solid {DARK_BORDER}; "
            f"border-radius: 4px; padding: 8px; color: {ACCENT_GREEN}; "
            f"font-family: 'Consolas', 'Monaco', monospace; font-size: 12px; }}"
        )
        layout.addWidget(self._log)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._restart_btn = QPushButton("立即重启")
        self._restart_btn.setVisible(False)
        self._restart_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT_GREEN}; color: white; "
            f"border: none; border-radius: 4px; padding: 8px 24px; font-size: 13px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: #6ab86a; }}"
        )
        self._restart_btn.clicked.connect(self._do_restart)
        btn_row.addWidget(self._restart_btn)

        self._close_btn = QPushButton("关闭")
        self._close_btn.setVisible(False)
        self._close_btn.setStyleSheet(
            f"QPushButton {{ background: {DARK_SURFACE}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {DARK_BORDER}; }}"
            f"QPushButton:hover {{ border-color: {ACCENT_BLUE}; }}"
        )
        self._close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

        self._sig = _UpdateSignal()
        self._sig.log.connect(self._append_log)
        self._sig.finished.connect(self._on_finished)

        t = threading.Thread(target=self._run_update, daemon=True)
        t.start()

    def _append_log(self, text: str):
        self._log.append(text)

    def _on_finished(self, success: bool, summary: str):
        if success:
            self._title.setText("✅ 更新成功")
            self._title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {ACCENT_GREEN};")
            self._restart_btn.setVisible(True)
        else:
            self._title.setText("⚠️ 更新失败")
            self._title.setStyleSheet("font-size: 15px; font-weight: bold; color: #ff6b8a;")
        self._close_btn.setVisible(True)

    def _run_update(self):
        try:
            self._sig.log.emit("正在检测 git remote...")
            remote_result = subprocess.run(
                ["git", "remote"], cwd=_SCRIPT_DIR,
                capture_output=True, text=True, timeout=5,
            )
            remote = remote_result.stdout.strip().split('\n')[0] if remote_result.returncode == 0 else "origin"

            cmd = f"git pull {remote} main"
            self._sig.log.emit(f"\n$ {cmd}\n")

            result = subprocess.run(
                ["git", "pull", remote, "main"], cwd=_SCRIPT_DIR,
                capture_output=True, text=True, timeout=60,
            )
            if result.stdout.strip():
                self._sig.log.emit(result.stdout.strip())
            if result.stderr.strip():
                self._sig.log.emit(result.stderr.strip())

            if result.returncode == 0:
                set_update_flag(False)
                self._sig.log.emit("\n✅ 更新完成，点击「立即重启」应用更新")
                self._sig.finished.emit(True, "")
            else:
                self._sig.log.emit(f"\n⚠️ git 返回码: {result.returncode}")
                self._sig.finished.emit(False, "")
        except Exception as e:
            self._sig.log.emit(f"\n⚠️ 异常: {e}")
            self._sig.finished.emit(False, str(e))

    def _do_restart(self):
        python = sys.executable
        daemon_script = os.path.join(_SCRIPT_DIR, "feedback_daemon.py")
        if os.path.exists(daemon_script):
            subprocess.Popen([python, daemon_script], start_new_session=True)
        from PySide6.QtWidgets import QApplication
        QApplication.quit()
