"""Settings dialog and version checker for MCP Feedback Assistant."""
import os
import threading
import urllib.request
import urllib.error

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QGroupBox, QFrame, QTextEdit,
)
from PySide6.QtCore import Qt, QSettings, Signal, QObject

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


# ── Settings keys ──────────────────────────────────────────────────────

SETTINGS_ORG = "InteractiveFeedbackMCP"
SETTINGS_APP = "Settings"

KEY_CHINESE_DEFAULT = "chinese_mode_default"
KEY_REREAD_RULES_DEFAULT = "reread_rules_default"
KEY_CHECK_UPDATE = "check_update_on_start"
KEY_CUSTOM_SUFFIX = "custom_suffix_text"


def load_settings() -> dict:
    s = QSettings(SETTINGS_ORG, SETTINGS_APP)
    return {
        KEY_CHINESE_DEFAULT: s.value(KEY_CHINESE_DEFAULT, True, type=bool),
        KEY_REREAD_RULES_DEFAULT: s.value(KEY_REREAD_RULES_DEFAULT, False, type=bool),
        KEY_CHECK_UPDATE: s.value(KEY_CHECK_UPDATE, True, type=bool),
        KEY_CUSTOM_SUFFIX: s.value(KEY_CUSTOM_SUFFIX, "", type=str),
    }


def save_settings(data: dict):
    s = QSettings(SETTINGS_ORG, SETTINGS_APP)
    for k, v in data.items():
        s.setValue(k, v)


# ── Settings Dialog ────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MCP 反馈助手 - 设置")
        self.setMinimumWidth(420)
        _check_svg = os.path.join(_SCRIPT_DIR, "images", "check-blue.svg").replace("\\", "/")
        self.setStyleSheet(f"""
            QDialog {{ background-color: {DARK_BG}; color: {TEXT_PRIMARY}; }}
            QCheckBox {{ spacing: 8px; font-size: 13px; color: {TEXT_PRIMARY}; padding: 4px 0; }}
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

        frame_defaults = QFrame()
        frame_defaults.setStyleSheet(_section_style)
        gl = QVBoxLayout(frame_defaults)
        gl.setContentsMargins(10, 8, 10, 8)
        self.cb_chinese = QCheckBox("使用中文（默认勾选）")
        self.cb_reread = QCheckBox("重新读取Rules（默认勾选）")
        self.cb_update = QCheckBox("启动时检查更新")
        gl.addWidget(self.cb_chinese)
        gl.addWidget(self.cb_reread)
        gl.addWidget(self.cb_update)
        layout.addWidget(frame_defaults)

        # Custom suffix
        lbl_suffix = QLabel("自定义追加文本")
        lbl_suffix.setStyleSheet(_section_title_style)
        layout.addWidget(lbl_suffix)

        hint = QLabel("每次提交反馈时自动追加的文本（留空则不追加）：")
        hint.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
        layout.addWidget(hint)
        self.suffix_edit = QTextEdit()
        self.suffix_edit.setMaximumHeight(80)
        self.suffix_edit.setPlaceholderText("例如：请使用简洁的语言回复")
        layout.addWidget(self.suffix_edit)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        check_btn = QPushButton("检查更新")
        check_btn.setStyleSheet(
            f"QPushButton {{ background: {DARK_SURFACE}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {DARK_BORDER}; }} QPushButton:hover {{ border-color: {ACCENT_BLUE}; }}"
        )
        check_btn.clicked.connect(self._do_check_update)
        btn_layout.addWidget(check_btn)

        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._save_and_close)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

        self._load()

    def _load(self):
        data = load_settings()
        self.cb_chinese.setChecked(data[KEY_CHINESE_DEFAULT])
        self.cb_reread.setChecked(data[KEY_REREAD_RULES_DEFAULT])
        self.cb_update.setChecked(data[KEY_CHECK_UPDATE])
        self.suffix_edit.setPlainText(data[KEY_CUSTOM_SUFFIX])

    def _save_and_close(self):
        save_settings({
            KEY_CHINESE_DEFAULT: self.cb_chinese.isChecked(),
            KEY_REREAD_RULES_DEFAULT: self.cb_reread.isChecked(),
            KEY_CHECK_UPDATE: self.cb_update.isChecked(),
            KEY_CUSTOM_SUFFIX: self.suffix_edit.toPlainText().strip(),
        })
        self.accept()

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
            self._update_label.setText(
                f"🔄 有新版本: {remote_ver}（当前: {local_version()}）\n"
                f"运行 cd {_SCRIPT_DIR} && git pull 更新"
            )
            self._update_label.setStyleSheet(f"color: #f0a050; font-size: 12px;")
        self._update_label.setVisible(True)
