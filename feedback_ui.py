# Interactive Feedback MCP UI
# Developed by Fábio Ferreira (https://x.com/fabiomlferreira)
# Enhanced by Pau Oliva (https://x.com/pof)
# UI restyled to match MCP 反馈助手 dark theme
import os
import sys
import json
import argparse
import subprocess
from typing import TypedDict, Optional, List

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QTextEdit, QGroupBox,
    QFrame, QScrollArea, QFileDialog, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QTimer, QSettings, QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QIcon, QKeyEvent, QPalette, QColor, QPixmap, QImage

from settings_dialog import (
    SettingsDialog, load_settings, KEY_CHINESE_DEFAULT,
    KEY_REREAD_RULES_DEFAULT, KEY_CUSTOM_SUFFIX, BadgePushButton,
    has_update_flag, get_auto_reply_seconds, AUTO_REPLY_MESSAGE,
    DEFAULT_AUTO_REPLY_SECONDS,
)


class FeedbackResult(TypedDict):
    interactive_feedback: str
    images: List[str]
    mentioned_entities: List[dict]


DARK_BG = "#1e1e2e"
DARK_SURFACE = "#2a2a3e"
DARK_BORDER = "#3a3a5a"
ACCENT_BLUE = "#6cacfe"
ACCENT_GREEN = "#7ec87e"
ACCENT_ORANGE = "#f0a050"
TEXT_PRIMARY = "#e0e0e0"
TEXT_SECONDARY = "#999"
BTN_SUBMIT_BG = "#4a9eff"
BTN_SUBMIT_HOVER = "#5ab0ff"
BTN_CANCEL_BG = "#ff6b8a"
BTN_CANCEL_HOVER = "#ff8da6"


_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CHECK_ICON = os.path.join(_SCRIPT_DIR, "images", "check-blue.svg").replace("\\", "/")
_CLOSE_ICON = os.path.join(_SCRIPT_DIR, "images", "close.svg").replace("\\", "/")
_CLOSE_HOVER_ICON = os.path.join(_SCRIPT_DIR, "images", "close-hover.svg").replace("\\", "/")

GLOBAL_STYLE = f"""
QMainWindow {{
    background-color: {DARK_BG};
}}
QWidget {{
    color: {TEXT_PRIMARY};
    font-size: 13px;
}}
QGroupBox {{
    background-color: transparent;
    border: none;
    margin-top: 6px;
    padding: 0;
    font-size: 13px;
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0;
}}
QTextEdit {{
    background-color: {DARK_SURFACE};
    border: 1px solid {DARK_BORDER};
    border-radius: 6px;
    padding: 10px;
    color: {TEXT_PRIMARY};
    font-size: 13px;
    selection-background-color: #4a9eff;
}}
QScrollArea {{
    border: 1px solid {DARK_BORDER};
    border-radius: 6px;
    background-color: {DARK_SURFACE};
}}
QCheckBox {{
    spacing: 8px;
    font-size: 13px;
    padding: 6px 10px;
    border: 1px solid {DARK_BORDER};
    border-radius: 6px;
    background-color: {DARK_SURFACE};
}}
QCheckBox:hover {{
    background-color: #333350;
    border-color: {ACCENT_BLUE};
}}
QCheckBox:checked {{
    background-color: rgba(74, 158, 255, 0.15);
    border-color: {ACCENT_BLUE};
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 2px solid {DARK_BORDER};
    background-color: {DARK_SURFACE};
}}
QCheckBox::indicator:checked {{
    border-color: {ACCENT_BLUE};
    background-color: {DARK_SURFACE};
    image: url({_CHECK_ICON});
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT_BLUE};
}}
QTabWidget::pane {{
    border: 1px solid {DARK_BORDER};
    background-color: {DARK_BG};
    border-radius: 4px;
}}
QTabBar::tab {{
    background-color: {DARK_SURFACE};
    color: {TEXT_SECONDARY};
    border: 1px solid {DARK_BORDER};
    border-bottom: none;
    padding: 6px 16px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    font-size: 12px;
}}
QTabBar::tab:selected {{
    background-color: {DARK_BG};
    color: {ACCENT_BLUE};
    font-weight: bold;
}}
QTabBar::tab:hover {{
    background-color: #333350;
    color: {TEXT_PRIMARY};
}}
QTabBar::close-button {{
    image: url({_CLOSE_ICON});
    subcontrol-position: right;
    border: none;
    padding: 3px;
}}
QTabBar::close-button:hover {{
    image: url({_CLOSE_HOVER_ICON});
}}
"""


class ClickableCheckBox(QCheckBox):
    """QCheckBox that toggles on click anywhere within its bounding rect."""
    def hitButton(self, pos):
        return self.rect().contains(pos)


class FeedbackTextEdit(QTextEdit):
    image_pasted = Signal(QImage)
    submit_requested = Signal()
    at_typed = Signal()  # emitted when user types '@'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        self._mention_tracker = None
        self._suppress_at_detect = False
        self._prev_text = ""
        self.textChanged.connect(self._check_at_input)

    def set_mention_tracker(self, tracker):
        self._mention_tracker = tracker

    def _check_at_input(self):
        if self._suppress_at_detect:
            return
        text = self.toPlainText()
        if len(text) > len(self._prev_text):
            new_part = text[len(self._prev_text):]
            if "@" in new_part:
                self._prev_text = text
                self.at_typed.emit()
                return
        self._prev_text = text

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Return and event.modifiers() == Qt.ControlModifier:
            self.submit_requested.emit()
        elif event.key() == Qt.Key_V and event.modifiers() == Qt.ControlModifier:
            clipboard = QApplication.clipboard()
            if clipboard.mimeData() and clipboard.mimeData().hasImage():
                image = clipboard.image()
                if not image.isNull():
                    self.image_pasted.emit(image)
                    return
            super().keyPressEvent(event)
        elif event.key() == Qt.Key_Backspace and self._mention_tracker:
            from mention_completer import MentionTracker
            if MentionTracker.handle_backspace(self):
                return
            super().keyPressEvent(event)
        else:
            super().keyPressEvent(event)


class ImageZoomDialog(QWidget):
    """Fullscreen overlay to show a zoomed image."""

    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setAlignment(Qt.AlignCenter)

        overlay = QWidget(self)
        overlay.setStyleSheet("background-color: rgba(0, 0, 0, 200);")
        overlay.setGeometry(0, 0, screen.width(), screen.height())
        overlay.lower()

        label = QLabel()
        max_w = screen.width() - 80
        max_h = screen.height() - 80
        scaled = pixmap.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        label.setPixmap(scaled)
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)

        hint = QLabel("\u70b9\u51fb\u4efb\u610f\u4f4d\u7f6e\u5173\u95ed")
        hint.setStyleSheet("color: rgba(255,255,255,150); font-size: 12px;")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

    def mousePressEvent(self, event):
        self.close()

    def keyPressEvent(self, event):
        self.close()


class ScreenshotThumbnail(QWidget):
    removed = Signal(int)

    def __init__(self, pixmap: QPixmap, index: int, parent=None):
        super().__init__(parent)
        self.index = index
        self.full_pixmap = pixmap
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(44, 44)

        self.thumb_label = QLabel(self)
        scaled = pixmap.scaled(36, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.thumb_label.setPixmap(scaled)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setStyleSheet(
            f"border-radius: 4px; background: {DARK_SURFACE};"
        )
        self.thumb_label.setFixedSize(40, 40)
        self.thumb_label.move(2, 2)

        self.remove_btn = QPushButton("\u2715", self)
        self.remove_btn.setFixedSize(16, 16)
        self.remove_btn.move(28, 0)
        self.remove_btn.setStyleSheet(
            f"QPushButton {{ background: {BTN_CANCEL_BG}; color: white; "
            f"border: none; border-radius: 8px; font-size: 9px; padding: 0; }}"
            f"QPushButton:hover {{ background: #ff4060; }}"
        )
        self.remove_btn.clicked.connect(lambda: self.removed.emit(self.index))
        self.remove_btn.setVisible(False)

    def enterEvent(self, event):
        self.remove_btn.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.remove_btn.setVisible(False)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._show_zoom()

    def _show_zoom(self):
        self._zoom_dialog = ImageZoomDialog(self.full_pixmap)
        self._zoom_dialog.show()


class FeedbackContentWidget(QWidget):
    """Reusable feedback form widget - used both standalone and as tab content."""
    feedback_submitted = Signal(dict)

    def __init__(self, prompt: str, predefined_options: Optional[List[str]] = None,
                 countdown_seconds: int = 0, parent=None):
        super().__init__(parent)
        self.prompt = prompt
        self.predefined_options = predefined_options or []
        self.screenshots: List[QPixmap] = []
        self._countdown_total = countdown_seconds
        self._countdown_remaining = countdown_seconds
        self._feishu_client = None
        self._mention_tracker = None
        self._create_ui()

    def set_feishu_client(self, client):
        """Connect a FeishuClient for @ mention support."""
        if client is None:
            return
        try:
            from mention_completer import MentionTracker
            self._feishu_client = client
            self._mention_tracker = MentionTracker()
            if hasattr(self, 'feedback_text'):
                self.feedback_text.set_mention_tracker(self._mention_tracker)
            if hasattr(self, '_mention_btn'):
                self._mention_btn.setVisible(True)
        except Exception:
            import traceback
            traceback.print_exc()

    def _create_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 8, 12, 8)
        main_layout.setSpacing(6)

        # Countdown timer display
        self._countdown_label = QLabel("")
        self._countdown_label.setAlignment(Qt.AlignCenter)
        self._countdown_label.setWordWrap(True)
        self._countdown_label.setStyleSheet(
            f"background-color: rgba(240, 160, 80, 0.15); "
            f"border: 1px solid {ACCENT_ORANGE}; border-radius: 6px; "
            f"padding: 8px 12px; color: {ACCENT_ORANGE}; font-size: 13px; font-weight: bold;"
        )
        if self._countdown_total > 0:
            self._update_countdown_text()
            self._countdown_label.setVisible(True)
            self._countdown_timer = QTimer(self)
            self._countdown_timer.setInterval(1000)
            self._countdown_timer.timeout.connect(self._on_countdown_tick)
            self._countdown_timer.start()
        else:
            self._countdown_label.setVisible(False)
            self._countdown_timer = None
        main_layout.addWidget(self._countdown_label)

        summary_title = QLabel("\U0001f916 AI \u5de5\u4f5c\u6458\u8981")
        summary_title.setStyleSheet(
            f"color: {ACCENT_BLUE}; font-size: 14px; font-weight: bold; "
            f"padding: 2px 0; margin: 0;"
        )
        main_layout.addWidget(summary_title)

        self.description_text = QTextEdit()
        self.description_text.setPlainText(self.prompt)
        self.description_text.setReadOnly(True)
        self.description_text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.description_text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.description_text.document().setDocumentMargin(8)
        font_h = self.description_text.fontMetrics().height()
        self.description_text.setMinimumHeight(20 * font_h + 16)
        self.description_text.setMaximumHeight(40 * font_h + 16)
        self.description_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        main_layout.addWidget(self.description_text)

        self.option_checkboxes = []
        if self.predefined_options and len(self.predefined_options) > 0:
            options_title = QLabel("\U0001f4cb \u9009\u9879")
            options_title.setStyleSheet(
                f"color: {ACCENT_GREEN}; font-size: 14px; font-weight: bold; "
                f"padding: 2px 0; margin-top: 4px;"
            )
            main_layout.addWidget(options_title)

            options_container = QFrame()
            options_container.setStyleSheet(
                f"QFrame {{ background-color: {DARK_SURFACE}; "
                f"border: 1px solid {DARK_BORDER}; border-radius: 6px; }}"
            )
            options_layout = QVBoxLayout(options_container)
            options_layout.setContentsMargins(10, 8, 10, 8)
            options_layout.setSpacing(4)
            for option in self.predefined_options:
                checkbox = ClickableCheckBox(option)
                self.option_checkboxes.append(checkbox)
                options_layout.addWidget(checkbox)
            main_layout.addWidget(options_container)

        feedback_header = QHBoxLayout()
        feedback_title = QLabel("\U0001f4dd \u60a8\u7684\u53cd\u9988")
        feedback_title.setStyleSheet(
            f"color: {ACCENT_ORANGE}; font-size: 14px; font-weight: bold; "
            f"padding: 2px 0; margin-top: 4px;"
        )
        feedback_header.addWidget(feedback_title)
        feedback_header.addStretch()

        self.img_count_label = QLabel("")
        self.img_count_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
        self.img_count_label.setVisible(False)
        feedback_header.addWidget(self.img_count_label)

        _action_btn_style = (
            f"QPushButton {{ background: transparent; color: {TEXT_SECONDARY}; "
            f"border: 1px solid {DARK_BORDER}; border-radius: 4px; padding: 3px 10px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,0.05); color: {TEXT_PRIMARY}; border-color: {ACCENT_BLUE}; }}"
        )

        self._mention_btn = QPushButton("@ \u63d0\u53ca")
        self._mention_btn.setStyleSheet(_action_btn_style)
        self._mention_btn.setToolTip("\u641c\u7d22\u98de\u4e66\u7528\u6237/\u7fa4/\u90e8\u95e8")
        self._mention_btn.clicked.connect(self._open_mention_dialog)
        self._mention_btn.setVisible(False)
        feedback_header.addWidget(self._mention_btn)

        add_img_btn = QPushButton("\U0001f4ce \u6dfb\u52a0\u56fe\u7247")
        add_img_btn.setStyleSheet(_action_btn_style)
        add_img_btn.clicked.connect(self._browse_image)
        feedback_header.addWidget(add_img_btn)
        main_layout.addLayout(feedback_header)

        self.feedback_text = FeedbackTextEdit()
        self.feedback_text.image_pasted.connect(self._on_image_pasted)
        self.feedback_text.submit_requested.connect(self._submit_feedback)
        self.feedback_text.at_typed.connect(self._on_at_typed)
        font_metrics = self.feedback_text.fontMetrics()
        row_height = font_metrics.height()
        padding = self.feedback_text.contentsMargins().top() + self.feedback_text.contentsMargins().bottom() + 5
        self.feedback_text.setMinimumHeight(5 * row_height + padding)
        self.feedback_text.setPlaceholderText("\u8bf7\u8f93\u5165\u60a8\u7684\u53cd\u9988...\n\nCtrl+Enter \u63d0\u4ea4")
        main_layout.addWidget(self.feedback_text)

        self.thumbnails_container = QWidget()
        self.thumbnails_container.setVisible(False)
        self.thumbnails_layout = QHBoxLayout(self.thumbnails_container)
        self.thumbnails_layout.setAlignment(Qt.AlignLeft)
        self.thumbnails_layout.setContentsMargins(0, 4, 0, 0)
        self.thumbnails_layout.setSpacing(4)
        main_layout.addWidget(self.thumbnails_container)

        hint_label = QLabel("Ctrl+Enter \u63d0\u4ea4")
        hint_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px; padding: 2px 0;")
        hint_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        bottom_layout = QHBoxLayout()

        self._settings_btn = BadgePushButton("⚙")
        self._settings_btn.setFixedSize(36, 36)
        self._settings_btn.setToolTip("设置")
        self._settings_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {TEXT_SECONDARY}; "
            f"border: 1px solid {DARK_BORDER}; border-radius: 4px; font-size: 20px; padding: 0; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,0.05); color: {TEXT_PRIMARY}; border-color: {ACCENT_BLUE}; }}"
        )
        self._settings_btn.clicked.connect(self._open_settings)
        if has_update_flag():
            self._settings_btn.set_badge(True)
        bottom_layout.addWidget(self._settings_btn)

        bottom_layout.addWidget(hint_label)
        bottom_layout.addStretch()

        _mini_cb_style = (
            f"QCheckBox {{ spacing: 6px; font-size: 12px; padding: 4px 8px; "
            f"border: 1px solid {DARK_BORDER}; border-radius: 4px; "
            f"background-color: {DARK_SURFACE}; }}"
            f"QCheckBox:hover {{ background-color: #333350; border-color: {ACCENT_BLUE}; }}"
            f"QCheckBox:checked {{ background-color: rgba(74, 158, 255, 0.15); border-color: {ACCENT_BLUE}; }}"
            f"QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 3px; "
            f"border: 2px solid {DARK_BORDER}; background-color: {DARK_SURFACE}; }}"
            f"QCheckBox::indicator:checked {{ border-color: {ACCENT_BLUE}; background-color: {DARK_SURFACE}; image: url({_CHECK_ICON}); }}"
            f"QCheckBox::indicator:hover {{ border-color: {ACCENT_BLUE}; }}"
        )

        user_prefs = load_settings()

        self.reread_rules_cb = ClickableCheckBox("重新读取Rules")
        self.reread_rules_cb.setChecked(user_prefs.get(KEY_REREAD_RULES_DEFAULT, False))
        self.reread_rules_cb.setStyleSheet(_mini_cb_style)
        bottom_layout.addWidget(self.reread_rules_cb)

        self.chinese_mode_cb = ClickableCheckBox("使用中文")
        self.chinese_mode_cb.setChecked(user_prefs.get(KEY_CHINESE_DEFAULT, True))
        self.chinese_mode_cb.setStyleSheet(_mini_cb_style)
        bottom_layout.addWidget(self.chinese_mode_cb)

        self.submit_btn = QPushButton("\u2705 \u63d0\u4ea4\u53cd\u9988")
        self.submit_btn.setMinimumWidth(120)
        self.submit_btn.setMinimumHeight(36)
        self._submit_btn_style_enabled = (
            f"QPushButton {{ background: {BTN_SUBMIT_BG}; color: white; "
            f"border: none; border-radius: 6px; padding: 8px 24px; font-size: 14px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {BTN_SUBMIT_HOVER}; }}"
        )
        self._submit_btn_style_disabled = (
            f"QPushButton {{ background: #444466; color: #888888; "
            f"border: none; border-radius: 6px; padding: 8px 24px; font-size: 14px; font-weight: bold; }}"
        )
        self.submit_btn.clicked.connect(self._submit_feedback)
        bottom_layout.addWidget(self.submit_btn)

        main_layout.addLayout(bottom_layout)

        self.feedback_text.textChanged.connect(self._update_submit_state)
        for cb in self.option_checkboxes:
            cb.toggled.connect(self._update_submit_state)
        self._update_submit_state()

    def _on_at_typed(self):
        """Triggered when user types '@' — open mention dialog if feishu is connected."""
        if not self._feishu_client:
            return
        cursor = self.feedback_text.textCursor()
        pos = cursor.position()
        if pos > 0:
            check = self.feedback_text.toPlainText()
            if check[pos - 1:pos] == "@":
                cursor.setPosition(pos - 1)
                cursor.setPosition(pos, cursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
                self.feedback_text.setTextCursor(cursor)
        self._open_mention_dialog()

    def _open_mention_dialog(self):
        if not self._feishu_client:
            return
        try:
            from mention_completer import MentionDialog
            self.feedback_text._suppress_at_detect = True
            dlg = MentionDialog(self._feishu_client, self)
            dlg.mention_selected.connect(self._on_mention_selected)
            dlg.exec()
            self.feedback_text._suppress_at_detect = False
            self.feedback_text._prev_text = self.feedback_text.toPlainText()
        except Exception:
            import traceback
            traceback.print_exc()
            self.feedback_text._suppress_at_detect = False

    def _on_mention_selected(self, entity: dict):
        if self._mention_tracker:
            self._mention_tracker.insert_mention(self.feedback_text, entity)

    def _on_image_pasted(self, image: QImage):
        pixmap = QPixmap.fromImage(image)
        if not pixmap.isNull():
            self._add_screenshot(pixmap)

    def _browse_image(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "\u9009\u62e9\u56fe\u7247", "",
            "\u56fe\u7247\u6587\u4ef6 (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;\u6240\u6709\u6587\u4ef6 (*)"
        )
        for path in file_paths:
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                self._add_screenshot(pixmap)

    def _add_screenshot(self, pixmap: QPixmap):
        max_size = 1600
        if pixmap.width() > max_size or pixmap.height() > max_size:
            pixmap = pixmap.scaled(max_size, max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.screenshots.append(pixmap)
        self._update_thumbnails()

    def _remove_screenshot(self, index: int):
        if 0 <= index < len(self.screenshots):
            self.screenshots.pop(index)
            self._update_thumbnails()

    def _update_thumbnails(self):
        while self.thumbnails_layout.count():
            item = self.thumbnails_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        for i, pixmap in enumerate(self.screenshots):
            thumb = ScreenshotThumbnail(pixmap, i)
            thumb.removed.connect(self._remove_screenshot)
            self.thumbnails_layout.addWidget(thumb)

        count = len(self.screenshots)
        self.thumbnails_container.setVisible(count > 0)
        self.img_count_label.setVisible(count > 0)
        self.img_count_label.setText(f"{count} \u5f20\u56fe\u7247")

    @staticmethod
    def _pixmap_to_base64(pixmap: QPixmap) -> str:
        byte_array = QByteArray()
        buffer = QBuffer(byte_array)
        buffer.open(QIODevice.WriteOnly)
        pixmap.save(buffer, "PNG")
        buffer.close()
        return byte_array.toBase64().data().decode('ascii')

    def _open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec():
            prefs = load_settings()
            self.chinese_mode_cb.setChecked(prefs.get(KEY_CHINESE_DEFAULT, True))
            self.reread_rules_cb.setChecked(prefs.get(KEY_REREAD_RULES_DEFAULT, False))

    def _has_content(self) -> bool:
        if self.feedback_text.toPlainText().strip():
            return True
        return any(cb.isChecked() for cb in self.option_checkboxes)

    def _update_submit_state(self):
        enabled = self._has_content()
        self.submit_btn.setEnabled(enabled)
        self.submit_btn.setStyleSheet(
            self._submit_btn_style_enabled if enabled else self._submit_btn_style_disabled
        )

    def _submit_feedback(self):
        if not self._has_content():
            return

        feedback_text = self.feedback_text.toPlainText().strip()
        selected_options = []

        if self.option_checkboxes:
            for i, checkbox in enumerate(self.option_checkboxes):
                if checkbox.isChecked():
                    selected_options.append(self.predefined_options[i])

        final_feedback_parts = []
        if selected_options:
            final_feedback_parts.append("; ".join(selected_options))
        if feedback_text:
            final_feedback_parts.append(feedback_text)

        final_feedback = "\n\n".join(final_feedback_parts)

        suffixes = []
        if self.reread_rules_cb.isChecked():
            suffixes.append("重新读取Rules")
        if self.chinese_mode_cb.isChecked():
            suffixes.append("必须完全使用中文（简体）回复和思考")
        custom = load_settings().get(KEY_CUSTOM_SUFFIX, "")
        if custom:
            suffixes.append(custom)
        if suffixes:
            final_feedback += "\n\n" + "\n\n".join(suffixes)

        images_b64 = [self._pixmap_to_base64(p) for p in self.screenshots]

        mentioned = []
        if self._mention_tracker:
            mentioned = self._mention_tracker.get_mentioned_entities(
                self.feedback_text.toPlainText()
            )
            self._mention_tracker.clear()

        result = FeedbackResult(
            interactive_feedback=final_feedback,
            images=images_b64,
            mentioned_entities=mentioned,
        )
        self._stop_countdown()
        self.feedback_submitted.emit(result)

    def _update_countdown_text(self):
        r = self._countdown_remaining
        minutes, seconds = divmod(r, 60)
        if minutes > 0:
            time_str = f"{minutes} 分 {seconds} 秒"
        else:
            time_str = f"{seconds} 秒"
        self._countdown_label.setText(
            f"⏱️ 请在 {time_str} 内回复 | "
            f"倒计时结束后会自动回复，您编辑的内容将消失，请注意保存"
        )
        if r <= 60:
            self._countdown_label.setStyleSheet(
                f"background-color: rgba(255, 107, 138, 0.2); "
                f"border: 1px solid {BTN_CANCEL_BG}; border-radius: 6px; "
                f"padding: 8px 12px; color: {BTN_CANCEL_BG}; font-size: 13px; font-weight: bold;"
            )
        elif r <= 300:
            self._countdown_label.setStyleSheet(
                f"background-color: rgba(240, 160, 80, 0.2); "
                f"border: 1px solid {ACCENT_ORANGE}; border-radius: 6px; "
                f"padding: 8px 12px; color: {ACCENT_ORANGE}; font-size: 13px; font-weight: bold;"
            )

    def _on_countdown_tick(self):
        self._countdown_remaining -= 1
        if self._countdown_remaining <= 0:
            self._stop_countdown()
            self._auto_submit_heartbeat()
            return
        self._update_countdown_text()

    def _stop_countdown(self):
        if self._countdown_timer and self._countdown_timer.isActive():
            self._countdown_timer.stop()

    def _auto_submit_heartbeat(self):
        """Auto-submit the preset heartbeat message when countdown reaches 0."""
        result = FeedbackResult(
            interactive_feedback=AUTO_REPLY_MESSAGE,
            images=[],
        )
        self.feedback_submitted.emit(result)


class FeedbackUI(QMainWindow):
    """Standalone feedback window (legacy mode / fallback)."""

    def __init__(self, prompt: str, predefined_options: Optional[List[str]] = None,
                 window_id: str = "0", countdown_seconds: int = 0):
        super().__init__()
        self.feedback_result = None

        title = "MCP \u53cd\u9988\u52a9\u624b"
        if window_id and window_id != "0":
            title += f" #{window_id}"
        self.setWindowTitle(title)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(script_dir, "images", "feedback.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.settings = QSettings("InteractiveFeedbackMCP", "InteractiveFeedbackMCP")
        self.settings.beginGroup("MainWindow_General")
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(800, 700)
            screen = QApplication.primaryScreen().geometry()
            x = (screen.width() - 800) // 2
            y = (screen.height() - 700) // 2
            self.move(x, y)
        state = self.settings.value("windowState")
        if state:
            self.restoreState(state)
        self.settings.endGroup()

        self.content = FeedbackContentWidget(prompt, predefined_options,
                                              countdown_seconds=countdown_seconds)
        self.content.feedback_submitted.connect(self._on_submitted)
        self.setCentralWidget(self.content)

    def _on_submitted(self, result):
        self.feedback_result = result
        self.close()

    def closeEvent(self, event):
        self.settings.beginGroup("MainWindow_General")
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        self.settings.endGroup()
        super().closeEvent(event)

    def _activate_input_method(self):
        try:
            subprocess.run(["fcitx-remote", "-o"], timeout=2, capture_output=True)
        except Exception:
            pass

    def run(self) -> FeedbackResult:
        self.show()
        QTimer.singleShot(300, self._activate_input_method)
        QApplication.instance().exec()

        if not self.feedback_result:
            return FeedbackResult(interactive_feedback="", images=[], mentioned_entities=[])

        return self.feedback_result


def feedback_ui(prompt: str, predefined_options: Optional[List[str]] = None,
                output_file: Optional[str] = None, window_id: str = "0",
                countdown_seconds: int = 0) -> Optional[FeedbackResult]:
    app = QApplication.instance() or QApplication()
    app.setStyle("Fusion")
    app.setStyleSheet(GLOBAL_STYLE)
    ui = FeedbackUI(prompt, predefined_options, window_id=window_id,
                    countdown_seconds=countdown_seconds)
    result = ui.run()

    if output_file and result:
        os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else ".", exist_ok=True)
        with open(output_file, "w") as f:
            json.dump(result, f)
        return None

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the feedback UI")
    parser.add_argument("--prompt", default="I implemented the changes you requested.", help="The prompt to show to the user")
    parser.add_argument("--predefined-options", default="", help="Pipe-separated list of predefined options (|||)")
    parser.add_argument("--output-file", help="Path to save the feedback result as JSON")
    parser.add_argument("--window-id", default="0", help="Window identifier for multi-agent scenarios")
    parser.add_argument("--countdown", type=int, default=0, help="Countdown seconds before auto-reply (0 = disabled)")
    args = parser.parse_args()

    predefined_options = [opt for opt in args.predefined_options.split("|||") if opt] if args.predefined_options else None

    result = feedback_ui(args.prompt, predefined_options, args.output_file,
                         window_id=args.window_id, countdown_seconds=args.countdown)
    if result:
        print(f"\nFeedback received:\n{result['interactive_feedback']}")
        if result.get('images'):
            print(f"Screenshots attached: {len(result['images'])}")
    sys.exit(0)
