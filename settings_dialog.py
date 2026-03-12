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
    QCheckBox, QGroupBox, QFrame, QTextEdit, QSpinBox,
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
KEY_HAS_UPDATE = "has_update"

DEFAULT_TIMEOUT_MINUTES = 720  # 12 hours


def load_settings() -> dict:
    s = QSettings(SETTINGS_ORG, SETTINGS_APP)
    return {
        KEY_CHINESE_DEFAULT: s.value(KEY_CHINESE_DEFAULT, True, type=bool),
        KEY_REREAD_RULES_DEFAULT: s.value(KEY_REREAD_RULES_DEFAULT, False, type=bool),
        KEY_CHECK_UPDATE: s.value(KEY_CHECK_UPDATE, True, type=bool),
        KEY_CUSTOM_SUFFIX: s.value(KEY_CUSTOM_SUFFIX, "", type=str),
        KEY_TIMEOUT_MINUTES: s.value(KEY_TIMEOUT_MINUTES, DEFAULT_TIMEOUT_MINUTES, type=int),
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
    Buffer = 5% of total, capped between 10s and 200s."""
    s = QSettings(SETTINGS_ORG, SETTINGS_APP)
    mins = s.value(KEY_TIMEOUT_MINUTES, DEFAULT_TIMEOUT_MINUTES, type=int)
    total_sec = mins * 60
    buffer = max(10, min(200, int(total_sec * 0.05)))
    return total_sec - buffer


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

    def _load(self):
        data = load_settings()
        self.cb_chinese.setChecked(data[KEY_CHINESE_DEFAULT])
        self.cb_reread.setChecked(data[KEY_REREAD_RULES_DEFAULT])
        self.cb_update.setChecked(data[KEY_CHECK_UPDATE])
        self.suffix_edit.setPlainText(data[KEY_CUSTOM_SUFFIX])
        total_mins = data.get(KEY_TIMEOUT_MINUTES, DEFAULT_TIMEOUT_MINUTES)
        self.timeout_hours_spin.setValue(total_mins // 60)
        self.timeout_mins_spin.setValue(total_mins % 60)
        if self._has_update:
            self._check_btn.set_badge(True)

    def _save_and_close(self):
        total_mins = self.timeout_hours_spin.value() * 60 + self.timeout_mins_spin.value()
        if total_mins < 1:
            total_mins = 1
        save_settings({
            KEY_CHINESE_DEFAULT: self.cb_chinese.isChecked(),
            KEY_REREAD_RULES_DEFAULT: self.cb_reread.isChecked(),
            KEY_CHECK_UPDATE: self.cb_update.isChecked(),
            KEY_CUSTOM_SUFFIX: self.suffix_edit.toPlainText().strip(),
            KEY_TIMEOUT_MINUTES: total_mins,
        })
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
