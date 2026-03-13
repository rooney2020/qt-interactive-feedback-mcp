"""@ mention dialog for the feedback window.

Provides a search dialog with tabs for users and chats, queries FeishuClient
with debounced input, pagination, and avatar display.
"""

import threading
import urllib.request
from typing import Optional, List, Dict, Any

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit,
    QListWidget, QListWidgetItem, QLabel, QPushButton,
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QSize
from PySide6.QtGui import QTextCursor, QColor, QTextCharFormat, QPixmap, QIcon, QImage

_TYPE_ICONS = {"user": "👤", "chat": "💬"}
MENTION_COLOR = "#6cacfe"
_AVATAR_SIZE = 28
_AVATAR_CACHE: Dict[str, QIcon] = {}

_SCROLLBAR_STYLE = """
    QScrollBar:vertical {
        background: #1e1e2e; width: 8px; border: none; border-radius: 4px;
    }
    QScrollBar::handle:vertical {
        background: rgba(108,172,254,0.35); min-height: 30px; border-radius: 4px;
    }
    QScrollBar::handle:vertical:hover { background: rgba(108,172,254,0.55); }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
"""

_PAGE_SIZE = 10


class _SearchSignal(QObject):
    results_ready = Signal(dict)  # search_all return dict


class _AvatarSignal(QObject):
    avatar_ready = Signal(str, QImage)  # (open_id, image)


class MentionDialog(QDialog):
    """Modal dialog for searching Feishu users / chats with separate tabs."""

    mention_selected = Signal(dict)  # {"type", "name", "id", "subtitle"}

    def __init__(self, feishu_client, parent=None):
        super().__init__(parent)
        self._feishu = feishu_client
        self._sig = _SearchSignal()
        self._sig.results_ready.connect(self._on_results)
        self._avatar_sig = _AvatarSignal()
        self._avatar_sig.avatar_ready.connect(self._on_avatar_ready)
        self._page_token: str = ""
        self._items: List[Dict[str, Any]] = []

        self.setWindowTitle("@ 提及飞书实体")
        self.setMinimumSize(400, 460)

        _toggle_style = (
            "QPushButton {"
            "  background: #2a2a3e; color: #999; border: 1px solid #3a3a5a;"
            "  border-radius: 6px; padding: 6px 16px; font-size: 13px;"
            "}"
            "QPushButton:hover { color: #e0e0e0; border-color: #6cacfe; }"
            "QPushButton:checked {"
            "  background: rgba(108,172,254,0.15); color: #6cacfe;"
            "  border-color: #6cacfe; font-weight: bold;"
            "}"
        )
        self.setStyleSheet(
            "QDialog { background-color: #1e1e2e; color: #e0e0e0; }"
            "QLineEdit {"
            "  background-color: #2a2a3e; color: #e0e0e0;"
            "  border: 1px solid #3a3a5a; border-radius: 6px;"
            "  padding: 8px 12px; font-size: 14px;"
            "}"
            "QLineEdit:focus { border-color: #6cacfe; }"
            "QListWidget {"
            "  background-color: #2a2a3e; color: #e0e0e0;"
            "  border: 1px solid #3a3a5a; border-radius: 6px;"
            "  font-size: 13px; padding: 4px;"
            "}"
            "QListWidget::item {"
            "  padding: 8px 10px; border-radius: 4px;"
            "}"
            "QListWidget::item:hover, QListWidget::item:selected {"
            "  background-color: rgba(74,158,255,0.25);"
            "}"
            "QLabel { color: #999; font-size: 12px; }"
            + _SCROLLBAR_STYLE
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        toggle_row = QHBoxLayout()
        self._btn_user = QPushButton("👤 联系人")
        self._btn_user.setCheckable(True)
        self._btn_user.setChecked(True)
        self._btn_user.setStyleSheet(_toggle_style)
        self._btn_user.clicked.connect(self._select_user_mode)
        toggle_row.addWidget(self._btn_user)

        self._btn_chat = QPushButton("💬 群")
        self._btn_chat.setCheckable(True)
        self._btn_chat.setStyleSheet(_toggle_style)
        self._btn_chat.clicked.connect(self._select_chat_mode)
        toggle_row.addWidget(self._btn_chat)
        toggle_row.addStretch()
        layout.addLayout(toggle_row)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("搜索联系人...")
        self._search_input.setClearButtonEnabled(True)
        layout.addWidget(self._search_input)

        self._status = QLabel("")
        layout.addWidget(self._status)

        self._list = QListWidget()
        self._list.setIconSize(QSize(_AVATAR_SIZE, _AVATAR_SIZE))
        self._list.itemDoubleClicked.connect(self._on_item_selected)
        self._list.itemActivated.connect(self._on_item_selected)
        layout.addWidget(self._list)

        self._more_btn = QPushButton("展开更多")
        self._more_btn.setVisible(False)
        self._more_btn.setStyleSheet(
            "QPushButton {"
            "  background: transparent; color: #6cacfe; border: none;"
            "  font-size: 12px; padding: 4px;"
            "}"
            "QPushButton:hover { text-decoration: underline; }"
        )
        self._more_btn.clicked.connect(self._load_more)
        layout.addWidget(self._more_btn, alignment=Qt.AlignCenter)

        hint = QLabel("双击或按 Enter 选择 · Esc 关闭")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

        self._search_mode = "user"  # "user" or "chat"

        self._debounce = QTimer()
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self._do_search)
        self._search_input.textChanged.connect(self._on_input_changed)

        self._search_input.setFocus()

    def _select_user_mode(self):
        self._btn_user.setChecked(True)
        self._btn_chat.setChecked(False)
        if self._search_mode != "user":
            self._search_mode = "user"
            self._search_input.setPlaceholderText("搜索联系人...")
            self._list.setIconSize(QSize(_AVATAR_SIZE, _AVATAR_SIZE))
            self._reset_and_search()

    def _select_chat_mode(self):
        self._btn_chat.setChecked(True)
        self._btn_user.setChecked(False)
        if self._search_mode != "chat":
            self._search_mode = "chat"
            self._search_input.setPlaceholderText("搜索群...")
            self._list.setIconSize(QSize(0, 0))
            self._reset_and_search()

    def _reset_and_search(self):
        self._page_token = ""
        self._items: List[Dict[str, Any]] = []
        self._list.clear()
        self._more_btn.setVisible(False)
        if self._search_input.text().strip():
            self._do_search()

    def _on_input_changed(self, text: str):
        self._page_token = ""
        self._items = []
        if text.strip():
            self._debounce.start()
            self._status.setText("搜索中...")
        else:
            self._debounce.stop()
            self._list.clear()
            self._status.setText("")
            self._more_btn.setVisible(False)

    def _do_search(self):
        q = self._search_input.text().strip()
        if not q:
            return
        sig = self._sig
        token = self._page_token
        mode = self._search_mode
        feishu = self._feishu

        def _worker():
            try:
                if mode == "user":
                    result = feishu.search_users(q, _PAGE_SIZE, token)
                else:
                    result = feishu.search_chats(q, _PAGE_SIZE, token)
            except Exception:
                result = {"items": [], "page_token": "", "has_more": False}
            sig.results_ready.emit(result)

        threading.Thread(target=_worker, daemon=True).start()

    def _load_more(self):
        self._status.setText("加载更多...")
        self._more_btn.setVisible(False)
        self._do_search()

    def _on_results(self, result: Dict[str, Any]):
        new_items = result.get("items", [])
        self._page_token = result.get("page_token", "")
        has_more = result.get("has_more", False)

        self._items.extend(new_items)
        self._rebuild_list()
        self._more_btn.setVisible(has_more)

        if not self._items:
            self._status.setText("无结果")
        else:
            self._status.setText(f"找到 {len(self._items)} 个结果")

    def _rebuild_list(self):
        self._list.clear()

        if self._search_mode == "user":
            name_counts: Dict[str, int] = {}
            for u in self._items:
                n = u["name"]
                name_counts[n] = name_counts.get(n, 0) + 1

            for u in self._items:
                label = u["name"]
                if name_counts.get(u["name"], 0) > 1:
                    uid = u.get("user_id", "")
                    if uid:
                        label += f"  (ID: {uid})"
                sub = u.get("subtitle", "")
                if sub:
                    label += f"  —  {sub}"
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, u)
                cached = _AVATAR_CACHE.get(u.get("id", ""))
                if cached:
                    item.setIcon(cached)
                self._list.addItem(item)
            self._start_avatar_downloads()
        else:
            for c in self._items:
                label = c["name"]
                sub = c.get("subtitle", "")
                if sub:
                    label += f"  —  {sub}"
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, c)
                self._list.addItem(item)

        if self._items:
            self._list.setCurrentRow(0)

    def _start_avatar_downloads(self):
        sig = self._avatar_sig
        pending = [
            (u["id"], u["avatar_url"])
            for u in self._items
            if u.get("avatar_url") and u["id"] not in _AVATAR_CACHE
        ]
        if not pending:
            return

        def _worker():
            for open_id, url in pending:
                try:
                    req = urllib.request.Request(url)
                    req.add_header("User-Agent", "MCP-Feedback")
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        data = resp.read()
                    img = QImage()
                    img.loadFromData(data)
                    if not img.isNull():
                        sig.avatar_ready.emit(open_id, img)
                except Exception:
                    pass

        threading.Thread(target=_worker, daemon=True).start()

    def _on_avatar_ready(self, open_id: str, img: QImage):
        scaled = img.scaled(_AVATAR_SIZE, _AVATAR_SIZE,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        icon = QIcon(QPixmap.fromImage(scaled))
        _AVATAR_CACHE[open_id] = icon
        for row in range(self._list.count()):
            item = self._list.item(row)
            data = item.data(Qt.UserRole)
            if data and data.get("id") == open_id:
                item.setIcon(icon)

    def _on_item_selected(self, item: QListWidgetItem):
        if getattr(self, "_already_selected", False):
            return
        data = item.data(Qt.UserRole)
        if data:
            self._already_selected = True
            self.mention_selected.emit(data)
            self.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            cur = self._list.currentItem()
            if cur:
                self._on_item_selected(cur)
                return
        super().keyPressEvent(event)


MENTION_MARKER = QTextCharFormat.UserObject + 1


class MentionTracker:
    """Tracks inserted mentions and formats them as atomic blocks."""

    def __init__(self):
        self.mentions: List[Dict[str, Any]] = []

    def insert_mention(self, text_edit, entity: Dict[str, Any]):
        """Insert @Name at the current cursor position as an atomic block."""
        name = entity.get("name", "")
        mention_text = f"@{name}"

        cursor = text_edit.textCursor()
        start = cursor.position()

        fmt = QTextCharFormat()
        fmt.setForeground(QColor(MENTION_COLOR))
        fmt.setFontWeight(700)
        fmt.setProperty(MENTION_MARKER, entity.get("id", ""))
        cursor.insertText(mention_text, fmt)

        plain_fmt = QTextCharFormat()
        plain_fmt.setForeground(QColor("#e0e0e0"))
        plain_fmt.setFontWeight(400)
        cursor.insertText(" ", plain_fmt)
        text_edit.setTextCursor(cursor)
        text_edit.setFocus()

        self.mentions.append({
            "type": entity.get("type", "user"),
            "name": name,
            "id": entity.get("id", ""),
            "start": start,
            "length": len(mention_text),
        })

    @staticmethod
    def handle_backspace(text_edit) -> bool:
        """Intercept Backspace to delete entire mention block. Returns True if handled."""
        cursor = text_edit.textCursor()
        if cursor.hasSelection():
            return False

        pos = cursor.position()
        if pos == 0:
            return False

        check = QTextCursor(cursor)
        check.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.MoveAnchor)
        check.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor)
        fmt = check.charFormat()
        if not fmt.property(MENTION_MARKER):
            return False

        marker_id = fmt.property(MENTION_MARKER)
        start = pos - 1
        while start > 0:
            c = QTextCursor(cursor)
            c.setPosition(start - 1)
            c.setPosition(start, QTextCursor.MoveMode.KeepAnchor)
            if c.charFormat().property(MENTION_MARKER) == marker_id:
                start -= 1
            else:
                break
        end = pos
        doc_len = text_edit.document().characterCount() - 1
        while end < doc_len:
            c = QTextCursor(cursor)
            c.setPosition(end)
            c.setPosition(end + 1, QTextCursor.MoveMode.KeepAnchor)
            if c.charFormat().property(MENTION_MARKER) == marker_id:
                end += 1
            else:
                break

        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        text_edit.setTextCursor(cursor)
        return True

    def get_mentioned_entities(self, text: str = "") -> List[Dict[str, Any]]:
        """Return all tracked mentions. Uses text to validate positions when available."""
        seen_ids = set()
        result = []
        for m in self.mentions:
            mid = m["id"]
            if mid in seen_ids:
                continue
            expected = f"@{m['name']}"
            s = m["start"]
            if text and 0 <= s < len(text) and text[s:s + len(expected)] == expected:
                seen_ids.add(mid)
                result.append({"type": m["type"], "name": m["name"], "id": mid})
            elif not text:
                seen_ids.add(mid)
                result.append({"type": m["type"], "name": m["name"], "id": mid})
        if not result and self.mentions:
            for m in self.mentions:
                if m["id"] not in seen_ids:
                    seen_ids.add(m["id"])
                    result.append({"type": m["type"], "name": m["name"], "id": m["id"]})
        return result

    def clear(self):
        self.mentions.clear()
