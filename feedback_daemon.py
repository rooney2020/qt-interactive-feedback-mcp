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

from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget, QTabBar
from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtGui import QIcon

from feedback_ui import (
    GLOBAL_STYLE, FeedbackContentWidget, FeedbackResult,
    DARK_BG, DARK_BORDER, ACCENT_BLUE, TEXT_SECONDARY,
)

SOCKET_PATH = os.path.join("/tmp", "mcp_feedback_daemon.sock")
LOCK_PATH = os.path.join("/tmp", "mcp_feedback_daemon.lock")

request_queue: queue.Queue = queue.Queue()
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
    try:
        request = _recv_json(conn)
        session_id = request.get("session_id", "unknown")

        event = threading.Event()
        response_events[session_id] = event
        request_queue.put(request)

        while not event.wait(timeout=2.0):
            try:
                conn.setblocking(False)
                try:
                    peek = conn.recv(1, socket.MSG_PEEK)
                    if not peek:
                        response_events.pop(session_id, None)
                        return
                except BlockingIOError:
                    pass
                finally:
                    conn.setblocking(True)
            except (socket.error, OSError):
                response_events.pop(session_id, None)
                return

        response = response_dict.pop(session_id, {"interactive_feedback": "", "images": []})
        _send_json(conn, response)
    except Exception as e:
        print(f"[daemon] Error handling client: {e}", file=sys.stderr)
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _socket_server():
    """Run the socket server (called in a daemon thread)."""
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen(16)
    os.chmod(SOCKET_PATH, 0o700)

    while True:
        try:
            conn, _ = server.accept()
            threading.Thread(target=_handle_client, args=(conn,), daemon=True).start()
        except OSError:
            break


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

        self._session_tabs: Dict[str, FeedbackContentWidget] = {}

        self._poll_timer = QTimer()
        self._poll_timer.timeout.connect(self._poll_requests)
        self._poll_timer.start(100)

    def _poll_requests(self):
        """Check for new requests from the socket server thread."""
        while not request_queue.empty():
            try:
                data = request_queue.get_nowait()
            except queue.Empty:
                break
            self._add_tab(data)

    def _add_tab(self, data: dict):
        session_id = data.get("session_id", "unknown")
        message = data.get("message", "")
        options = data.get("predefined_options") or None
        tab_title = data.get("tab_title", f"\u4f1a\u8bdd #{session_id[:6]}")

        if isinstance(options, list):
            options = [str(o) for o in options if o]
            if not options:
                options = None

        tab = FeedbackContentWidget(message, options)
        tab.setProperty("session_id", session_id)
        tab.feedback_submitted.connect(lambda result, sid=session_id: self._on_tab_submitted(sid, result))

        index = self.tabs.addTab(tab, tab_title)
        self.tabs.setCurrentIndex(index)
        self._session_tabs[session_id] = tab

        if not self.isVisible():
            self.show()
        self.activateWindow()
        self.raise_()

        self._activate_input_method()

    def _on_tab_submitted(self, session_id: str, result: dict):
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

    def _on_tab_close_requested(self, index: int):
        tab = self.tabs.widget(index)
        if isinstance(tab, FeedbackContentWidget):
            session_id = tab.property("session_id")
            if session_id:
                self._session_tabs.pop(session_id, None)
                response_dict[session_id] = {"interactive_feedback": "", "images": []}
                evt = response_events.pop(session_id, None)
                if evt:
                    evt.set()
        self.tabs.removeTab(index)

        if self.tabs.count() == 0:
            self.hide()

    def _activate_input_method(self):
        try:
            subprocess.run(["fcitx-remote", "-o"], timeout=2, capture_output=True)
        except Exception:
            pass

    def closeEvent(self, event):
        self._settings.setValue("daemon_geometry", self.saveGeometry())
        for session_id in list(self._session_tabs.keys()):
            response_dict[session_id] = {"interactive_feedback": "", "images": []}
            evt = response_events.pop(session_id, None)
            if evt:
                evt.set()
        self._session_tabs.clear()
        self.hide()
        event.ignore()


def main():
    lock_fd = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        print("[daemon] Another instance is already running.", file=sys.stderr)
        sys.exit(1)

    lock_fd.write(str(os.getpid()))
    lock_fd.flush()

    srv_thread = threading.Thread(target=_socket_server, daemon=True)
    srv_thread.start()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(GLOBAL_STYLE)
    app.setQuitOnLastWindowClosed(False)

    env = os.environ
    if sys.platform == "linux":
        os.environ.setdefault("QT_IM_MODULE", "fcitx")
        os.environ.setdefault("XMODIFIERS", "@im=fcitx")

    window = DaemonWindow()

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
