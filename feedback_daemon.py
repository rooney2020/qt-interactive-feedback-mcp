#!/usr/bin/env python3
"""
MCP Feedback Daemon - Single window, multi-tab feedback UI.

Listens on a Unix domain socket for feedback requests from MCP server processes.
All feedback sessions are displayed as tabs in a single window.
"""
import os
import sys
import json
import socket
import threading
import queue
import signal
import fcntl
import subprocess
from typing import Dict, Optional

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QTabBar, QPushButton, QMenu, QSystemTrayIcon,
)
from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtGui import QIcon, QAction, QPixmap, QPainter, QColor

from feedback_ui import (
    GLOBAL_STYLE, FeedbackContentWidget, FeedbackResult,
    DARK_BG, DARK_BORDER, ACCENT_BLUE, TEXT_SECONDARY, TEXT_PRIMARY,
)
from settings_dialog import (
    SettingsDialog, load_settings, check_version_async,
    local_version, KEY_CHECK_UPDATE, BadgePushButton,
    set_update_flag, has_update_flag,
)

SOCKET_PATH = os.path.join("/tmp", "mcp_feedback_daemon.sock")
LOCK_PATH = os.path.join("/tmp", "mcp_feedback_daemon.lock")
LOG_PATH = os.path.join("/tmp", "mcp_feedback_daemon.log")

def _log(msg: str):
    """Write log message to both stderr and log file."""
    import datetime
    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] {msg}"
    try:
        print(f"[daemon] {line}", file=sys.stderr)
    except (BrokenPipeError, OSError):
        pass
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

request_queue: queue.Queue = queue.Queue()
close_queue: queue.Queue = queue.Queue()
response_dict: Dict[str, dict] = {}
response_events: Dict[str, threading.Event] = {}


def _recv_json(conn: socket.socket) -> dict:
    """Read a newline-terminated JSON message from a socket."""
    data = b""
    while True:
        chunk = conn.recv(4096)
        if not chunk:
            raise ConnectionError("Client disconnected")
        data += chunk
        if b"\n" in data:
            break
    return json.loads(data.decode("utf-8").strip())


def _send_json(conn: socket.socket, obj: dict):
    """Send a newline-terminated JSON message to a socket."""
    conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))


def _handle_client(conn: socket.socket):
    """Handle a single client connection in its own thread."""
    session_id = None
    try:
        request = _recv_json(conn)
        session_id = request.get("session_id", "unknown")

        event = threading.Event()
        response_events[session_id] = event
        request_queue.put(request)

        while not event.wait(timeout=0.5):
            try:
                conn.setblocking(False)
                try:
                    peek = conn.recv(1, socket.MSG_PEEK)
                    if not peek:
                        _log(f"Client disconnected for session {session_id}")
                        response_events.pop(session_id, None)
                        close_queue.put(session_id)
                        return
                except BlockingIOError:
                    pass
                finally:
                    conn.setblocking(True)
            except (socket.error, OSError):
                _log(f"Socket error for session {session_id}")
                response_events.pop(session_id, None)
                close_queue.put(session_id)
                return

        response = response_dict.pop(session_id, {"interactive_feedback": "", "images": []})
        resp_size = len(json.dumps(response, ensure_ascii=False).encode("utf-8"))
        img_count = len(response.get("images", []))
        _log(f"Sending response for {session_id}: {resp_size} bytes, {img_count} images")
        _send_json(conn, response)
        _log(f"Response sent successfully for {session_id}")
    except ConnectionError as e:
        _log(f"Client connection error for {session_id}: {e}")
        if session_id:
            response_events.pop(session_id, None)
            close_queue.put(session_id)
    except Exception as e:
        _log(f"Unexpected error handling client {session_id}: {type(e).__name__}: {e}")
        if session_id:
            response_events.pop(session_id, None)
            close_queue.put(session_id)
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _socket_server():
    """Run the socket server (called in a daemon thread)."""
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
        _log(f"Removed stale socket: {SOCKET_PATH}")

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen(16)
    os.chmod(SOCKET_PATH, 0o700)
    _log(f"Socket server listening on {SOCKET_PATH}")

    while True:
        try:
            conn, _ = server.accept()
            _log("Accepted new client connection")
            threading.Thread(target=_handle_client, args=(conn,), daemon=True).start()
        except OSError as e:
            _log(f"Socket server stopped: {e}")
            break


def _icon_with_badge(base_icon: QIcon, size: int = 64) -> QIcon:
    """Return a copy of base_icon with a red dot badge at top-right."""
    pixmap = base_icon.pixmap(size, size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = size // 4
    painter.setBrush(QColor("#e53935"))
    painter.setPen(QColor("#ff6b6b"))
    painter.drawEllipse(size - r - 1, 1, r, r)
    painter.end()
    return QIcon(pixmap)


class DaemonWindow(QMainWindow):
    """Single-window multi-tab feedback UI."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MCP \u53cd\u9988\u52a9\u624b")
        # Normal window level, not always-on-top

        script_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(script_dir, "images", "feedback.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self._feishu_client = None
        try:
            from feishu_client import FeishuClient
            self._feishu_client = FeishuClient()
            if self._feishu_client.is_configured:
                _log(f"FeishuClient loaded: {self._feishu_client.status_text}")
            else:
                _log("FeishuClient: not configured (app_id/app_secret not found)")
        except Exception as e:
            _log(f"FeishuClient init failed (non-fatal): {e}")

        self._settings = QSettings("InteractiveFeedbackMCP", "FeedbackDaemon")
        geo = self._settings.value("daemon_geometry")
        if geo:
            self.restoreGeometry(geo)
        else:
            self.resize(800, 700)
            screen = QApplication.primaryScreen().geometry()
            self.move((screen.width() - 800) // 2, (screen.height() - 700) // 2)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._on_tab_close_requested)
        self.setCentralWidget(self.tabs)

        # Tab bar corner: settings button
        self._corner_btn = BadgePushButton("⚙")
        self._corner_btn.setFixedSize(28, 28)
        self._corner_btn.setToolTip("设置")
        self._corner_btn_style_normal = (
            f"QPushButton {{ background: transparent; color: {TEXT_SECONDARY}; "
            f"border: none; font-size: 16px; padding: 0; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; }}"
        )
        self._corner_btn.setStyleSheet(self._corner_btn_style_normal)
        self._corner_btn.clicked.connect(self._open_settings)
        self.tabs.setCornerWidget(self._corner_btn, Qt.TopRightCorner)

        self._session_tabs: Dict[str, FeedbackContentWidget] = {}

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_requests)
        self._poll_timer.start(100)
        self._poll_count = 0
        self._last_poll_time = 0

        self._watchdog_timer = QTimer(self)
        self._watchdog_timer.timeout.connect(self._watchdog_check)
        self._watchdog_timer.start(60000)  # every 60s

        # System tray icon
        self._tray = None
        self._has_update = False
        self._setup_tray(icon_path)

        # Check for updates on startup
        self._ver_sig = None
        check_enabled = load_settings().get(KEY_CHECK_UPDATE, True)
        _log(f"Startup version check: enabled={check_enabled}")
        if check_enabled:
            self._ver_sig = check_version_async(self._on_startup_version_check)

    def _poll_requests(self):
        """Check for new requests and close requests from the socket server thread."""
        try:
            self._poll_count += 1
            if self._poll_count % 300 == 0:  # every 30s
                _log(f"Poll heartbeat #{self._poll_count}, queue={request_queue.qsize()}, close={close_queue.qsize()}, tabs={self.tabs.count()}, visible={self.isVisible()}")
            self._last_poll_time = self._poll_count

            while not request_queue.empty():
                try:
                    data = request_queue.get_nowait()
                except queue.Empty:
                    break
                self._add_tab(data)

            while not close_queue.empty():
                try:
                    session_id = close_queue.get_nowait()
                except queue.Empty:
                    break
                self._close_tab_by_session(session_id)
        except Exception as e:
            _log(f"CRITICAL: _poll_requests exception: {e}")

    def _add_tab(self, data: dict):
        session_id = data.get("session_id", "unknown")
        try:
            _log(f"Adding new tab for session {session_id}")
            message = data.get("message", "")
            options = data.get("predefined_options") or None
            tab_title = data.get("tab_title", f"\u4f1a\u8bdd #{session_id[:6]}")
            countdown = data.get("countdown_seconds", 0)

            if isinstance(options, list):
                options = [str(o) for o in options if o]
                if not options:
                    options = None

            tab = FeedbackContentWidget(message, options, countdown_seconds=countdown)
            tab.setProperty("session_id", session_id)
            if self._feishu_client:
                tab.set_feishu_client(self._feishu_client)
            tab.feedback_submitted.connect(lambda result, sid=session_id: self._on_tab_submitted(sid, result))

            index = self.tabs.addTab(tab, tab_title)
            self.tabs.setCurrentIndex(index)
            self._session_tabs[session_id] = tab

            self.setVisible(True)
            self.showNormal()
            self.activateWindow()
            self.raise_()

            self._activate_input_method()
        except Exception as e:
            _log(f"ERROR in _add_tab for {session_id}: {e}")
            response_dict[session_id] = {"interactive_feedback": f"[UI error: {e}]", "images": []}
            evt = response_events.pop(session_id, None)
            if evt:
                evt.set()

    def _close_tab_by_session(self, session_id: str):
        """Close a tab when the MCP client disconnects (e.g. Cursor timeout)."""
        try:
            tab = self._session_tabs.pop(session_id, None)
            if tab:
                index = self.tabs.indexOf(tab)
                if index >= 0:
                    self.tabs.removeTab(index)
                tab.deleteLater()
                _log(f"Closed orphaned tab for session {session_id}")

            if self.tabs.count() == 0:
                self.hide()
        except Exception as e:
            _log(f"ERROR in _close_tab_by_session for {session_id}: {e}")

    def _on_tab_submitted(self, session_id: str, result: dict):
        try:
            img_count = len(result.get("images", []))
            text_len = len(result.get("interactive_feedback", ""))
            _log(f"Tab submitted for {session_id}: text={text_len} chars, images={img_count}")
            tab = self._session_tabs.pop(session_id, None)
            if tab:
                index = self.tabs.indexOf(tab)
                if index >= 0:
                    self.tabs.removeTab(index)
                tab.deleteLater()

            response_dict[session_id] = result
            evt = response_events.pop(session_id, None)
            if evt:
                evt.set()

            if self.tabs.count() == 0:
                self.hide()
        except Exception as e:
            _log(f"ERROR in _on_tab_submitted for {session_id}: {e}")
            response_dict[session_id] = result or {"interactive_feedback": f"[submit error: {e}]", "images": []}
            evt = response_events.pop(session_id, None)
            if evt:
                evt.set()

    def _on_tab_close_requested(self, index: int):
        try:
            tab = self.tabs.widget(index)
            if isinstance(tab, FeedbackContentWidget):
                session_id = tab.property("session_id")
                _log(f"Tab close requested by user: index={index}, session={session_id}")
                if session_id:
                    self._session_tabs.pop(session_id, None)
                    response_dict[session_id] = {"interactive_feedback": "窗口可能被意外关闭，请发起新会话或重新连接", "images": []}
                    evt = response_events.pop(session_id, None)
                    if evt:
                        evt.set()
            self.tabs.removeTab(index)

            if self.tabs.count() == 0:
                _log("All tabs closed, hiding window")
                self.hide()
        except Exception as e:
            _log(f"ERROR in _on_tab_close_requested index={index}: {e}")

    def _setup_tray(self, icon_path: str):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            _log("System tray not available, skipping tray icon")
            return
        self._tray = QSystemTrayIcon(self)
        self._tray_base_icon = QIcon(icon_path) if icon_path and os.path.exists(icon_path) else QIcon()
        self._tray.setIcon(self._tray_base_icon)
        self._tray.setToolTip(f"MCP 反馈助手 v{local_version()}")
        self._tray.activated.connect(self._on_tray_activated)

        menu = QMenu()
        menu.setStyleSheet(
            f"QMenu {{ background-color: {DARK_BG}; color: {TEXT_PRIMARY}; border: 1px solid {DARK_BORDER}; }}"
            f"QMenu::item:selected {{ background-color: {ACCENT_BLUE}; }}"
        )
        show_action = QAction("显示窗口", self)
        show_action.triggered.connect(self._show_window)
        menu.addAction(show_action)

        settings_action = QAction("设置", self)
        settings_action.triggered.connect(self._open_settings)
        menu.addAction(settings_action)

        menu.addSeparator()

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.show()
        _log("System tray icon created")

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_window()

    def _show_window(self):
        self.setVisible(True)
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _open_settings(self):
        dlg = SettingsDialog(self, has_update=self._has_update)
        dlg.exec()

    def _on_startup_version_check(self, remote_ver: str, error: str):
        if error:
            _log(f"Version check failed: {error}")
            return
        if not remote_ver:
            _log("Version check returned empty")
            return
        local_v = local_version()
        _log(f"Version check: local={local_v}, remote={remote_ver}")
        if remote_ver != local_v:
            self._has_update = True
            set_update_flag(True)
            update_msg = f"有新版本 {remote_ver}（当前 {local_v}）"
            self.setWindowTitle(f"MCP 反馈助手 ⬆️ {update_msg}")
            # Red badge on tray icon
            if self._tray and hasattr(self, '_tray_base_icon'):
                self._tray.setIcon(_icon_with_badge(self._tray_base_icon))
                self._tray.setToolTip(f"MCP 反馈助手 - {update_msg}")
                self._tray.showMessage("MCP 反馈助手", update_msg,
                    QSystemTrayIcon.MessageIcon.Information, 8000)
            # Red badge on corner settings button and window icon
            self._corner_btn.set_badge(True)
            self._corner_btn.setToolTip(f"设置 - {update_msg}")
            if hasattr(self, '_tray_base_icon'):
                self.setWindowIcon(_icon_with_badge(self._tray_base_icon, 256))
            _log(f"Version update notification sent")

    def _watchdog_check(self):
        """Restart poll timer if it appears stuck."""
        expected_polls = self._poll_count
        if hasattr(self, '_prev_watchdog_count') and expected_polls == self._prev_watchdog_count:
            _log(f"WATCHDOG: poll timer appears stuck at {expected_polls}, restarting")
            self._poll_timer.stop()
            self._poll_timer.start(100)
        self._prev_watchdog_count = expected_polls

    def _activate_input_method(self):
        try:
            subprocess.run(["fcitx-remote", "-o"], timeout=2, capture_output=True)
        except Exception:
            pass

    def closeEvent(self, event):
        _log(f"Window closeEvent triggered, {len(self._session_tabs)} active sessions")
        self._settings.setValue("daemon_geometry", self.saveGeometry())
        _CLOSE_MSG = "窗口可能被意外关闭，请发起新会话或重新连接"
        for session_id in list(self._session_tabs.keys()):
            response_dict[session_id] = {"interactive_feedback": _CLOSE_MSG, "images": []}
            evt = response_events.pop(session_id, None)
            if evt:
                evt.set()
        self._session_tabs.clear()
        while self.tabs.count() > 0:
            w = self.tabs.widget(0)
            self.tabs.removeTab(0)
            if w:
                w.deleteLater()
        self.hide()
        event.ignore()


def _apply_ld_preload_if_needed():
    """Re-exec the process with LD_PRELOAD when .im_config.json requires it.

    On older systems (e.g. Ubuntu 20.04) the fcitx Qt6 plugin needs a newer
    libstdc++ than what the system provides.  LD_PRELOAD must be visible to the
    dynamic linker *before* the process loads any shared library, so we re-exec
    ourselves when the variable is not yet present.
    """
    im_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".im_config.json")
    if not os.path.exists(im_config_path):
        return

    try:
        import json as _json
        with open(im_config_path) as f:
            config = _json.load(f)

        im_module = config.get("im_module", "fcitx")
        os.environ.setdefault("QT_IM_MODULE", im_module)
        os.environ.setdefault("XMODIFIERS", f"@im={im_module}")

        ld_preload = config.get("ld_preload", "")
        if not ld_preload or not os.path.exists(ld_preload):
            return

        current = os.environ.get("LD_PRELOAD", "")
        if ld_preload in current:
            return  # already applied

        os.environ["LD_PRELOAD"] = f"{ld_preload}:{current}" if current else ld_preload
        _log(f"Re-executing with LD_PRELOAD={os.environ['LD_PRELOAD']}")
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        _log(f"_apply_ld_preload_if_needed failed: {e}")


def main():
    # Must run before QApplication — ensures LD_PRELOAD is active for Qt plugin loading
    _apply_ld_preload_if_needed()

    _log(f"Daemon starting, pid={os.getpid()}")
    lock_fd = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        _log("Another daemon instance is already running, exiting")
        print("[daemon] Another instance is already running.", file=sys.stderr)
        sys.exit(1)

    lock_fd.write(str(os.getpid()))
    lock_fd.flush()
    _log(f"Lock acquired: {LOCK_PATH}")

    srv_thread = threading.Thread(target=_socket_server, daemon=True)
    srv_thread.start()
    _log("Socket server thread started")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(GLOBAL_STYLE)
    app.setQuitOnLastWindowClosed(False)

    if sys.platform == "linux":
        im_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".im_config.json")
        im_module = "fcitx"
        if os.path.exists(im_config_path):
            try:
                import json as _json
                with open(im_config_path) as f:
                    im_module = _json.load(f).get("im_module", "fcitx")
            except Exception:
                pass
        os.environ.setdefault("QT_IM_MODULE", im_module)
        os.environ.setdefault("XMODIFIERS", f"@im={im_module}")

    window = DaemonWindow()
    _log("DaemonWindow created, entering event loop")

    def _shutdown(*_):
        app.quit()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    app.exec()

    try:
        lock_fd.close()
        os.unlink(LOCK_PATH)
    except OSError:
        pass
    if os.path.exists(SOCKET_PATH):
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass


if __name__ == "__main__":
    main()
