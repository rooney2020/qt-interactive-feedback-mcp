# Interactive Feedback MCP UI
# Developed by Fábio Ferreira (https://x.com/fabiomlferreira)
# Inspired by/related to dotcursorrules.com (https://dotcursorrules.com/)
# Enhanced by Pau Oliva (https://x.com/pof) with ideas from https://github.com/ttommyth/interactive-mcp
import os
import sys
import json
import argparse
from typing import TypedDict

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QTextEdit, QGroupBox,
    QFrame, QScrollArea, QFileDialog, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QTimer, QSettings, QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QIcon, QKeyEvent, QPalette, QColor, QPixmap, QImage

class FeedbackResult(TypedDict):
    interactive_feedback: str
    images: list[str]

def get_dark_mode_palette(app: QApplication):
    darkPalette = app.palette()
    darkPalette.setColor(QPalette.Window, QColor(53, 53, 53))
    darkPalette.setColor(QPalette.WindowText, Qt.white)
    darkPalette.setColor(QPalette.Disabled, QPalette.WindowText, QColor(127, 127, 127))
    darkPalette.setColor(QPalette.Base, QColor(42, 42, 42))
    darkPalette.setColor(QPalette.AlternateBase, QColor(66, 66, 66))
    darkPalette.setColor(QPalette.ToolTipBase, QColor(53, 53, 53))
    darkPalette.setColor(QPalette.ToolTipText, Qt.white)
    darkPalette.setColor(QPalette.Text, Qt.white)
    darkPalette.setColor(QPalette.Disabled, QPalette.Text, QColor(127, 127, 127))
    darkPalette.setColor(QPalette.Dark, QColor(35, 35, 35))
    darkPalette.setColor(QPalette.Shadow, QColor(20, 20, 20))
    darkPalette.setColor(QPalette.Button, QColor(53, 53, 53))
    darkPalette.setColor(QPalette.ButtonText, Qt.white)
    darkPalette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(127, 127, 127))
    darkPalette.setColor(QPalette.BrightText, Qt.red)
    darkPalette.setColor(QPalette.Link, QColor(42, 130, 218))
    darkPalette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    darkPalette.setColor(QPalette.Disabled, QPalette.Highlight, QColor(80, 80, 80))
    darkPalette.setColor(QPalette.HighlightedText, Qt.white)
    darkPalette.setColor(QPalette.Disabled, QPalette.HighlightedText, QColor(127, 127, 127))
    darkPalette.setColor(QPalette.PlaceholderText, QColor(127, 127, 127))
    return darkPalette

class FeedbackTextEdit(QTextEdit):
    image_pasted = Signal(QImage)

    def __init__(self, parent=None):
        super().__init__(parent)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Return and event.modifiers() == Qt.ControlModifier:
            parent = self.parent()
            while parent and not isinstance(parent, FeedbackUI):
                parent = parent.parent()
            if parent:
                parent._submit_feedback()
        elif event.key() == Qt.Key_V and event.modifiers() == Qt.ControlModifier:
            clipboard = QApplication.clipboard()
            if clipboard.mimeData() and clipboard.mimeData().hasImage():
                image = clipboard.image()
                if not image.isNull():
                    self.image_pasted.emit(image)
                    return
            super().keyPressEvent(event)
        else:
            super().keyPressEvent(event)

class ScreenshotThumbnail(QWidget):
    removed = Signal(int)

    def __init__(self, pixmap: QPixmap, index: int, parent=None):
        super().__init__(parent)
        self.index = index

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        thumb_label = QLabel()
        scaled = pixmap.scaled(150, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        thumb_label.setPixmap(scaled)
        thumb_label.setAlignment(Qt.AlignCenter)
        thumb_label.setStyleSheet("border: 1px solid #555; border-radius: 4px; padding: 2px;")
        layout.addWidget(thumb_label)

        remove_btn = QPushButton("✕")
        remove_btn.setFixedHeight(22)
        remove_btn.setStyleSheet(
            "QPushButton { color: #ff6666; background: transparent; "
            "border: 1px solid #555; border-radius: 3px; font-size: 11px; }"
            "QPushButton:hover { background: rgba(255,102,102,0.25); }"
        )
        remove_btn.clicked.connect(lambda: self.removed.emit(self.index))
        layout.addWidget(remove_btn)

        self.setFixedWidth(166)

class FeedbackUI(QMainWindow):
    def __init__(self, prompt: str, predefined_options: list[str] | None = None, window_id: str = "0"):
        super().__init__()
        self.prompt = prompt
        self.predefined_options = predefined_options or []
        self.feedback_result = None
        self.screenshots: list[QPixmap] = []

        title = "Interactive Feedback MCP"
        if window_id and window_id != "0":
            title += f" #{window_id}"
        self.setWindowTitle(title)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(script_dir, "images", "feedback.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        self.settings = QSettings("InteractiveFeedbackMCP", "InteractiveFeedbackMCP")

        self.settings.beginGroup("MainWindow_General")
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(800, 650)
            screen = QApplication.primaryScreen().geometry()
            x = (screen.width() - 800) // 2
            y = (screen.height() - 650) // 2
            self.move(x, y)
        state = self.settings.value("windowState")
        if state:
            self.restoreState(state)
        self.settings.endGroup()

        self._create_ui()

    def _create_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        self.feedback_group = QGroupBox()
        feedback_layout = QVBoxLayout(self.feedback_group)

        prompt_header = QHBoxLayout()
        prompt_title = QLabel("Message:")
        prompt_title.setStyleSheet("font-weight: bold; color: #ccc; font-size: 12px;")
        prompt_header.addWidget(prompt_title)
        prompt_header.addStretch()
        copy_btn = QPushButton("📋 Copy")
        copy_btn.setFixedHeight(24)
        copy_btn.setStyleSheet(
            "QPushButton { color: #aaa; background: transparent; "
            "border: 1px solid #555; border-radius: 3px; font-size: 11px; padding: 0 8px; }"
            "QPushButton:hover { background: rgba(42,130,218,0.25); color: #fff; }"
        )
        copy_btn.setToolTip("Copy message to clipboard")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self.prompt))
        prompt_header.addWidget(copy_btn)
        feedback_layout.addLayout(prompt_header)

        self.description_text = QTextEdit()
        self.description_text.setPlainText(self.prompt)
        self.description_text.setReadOnly(True)
        self.description_text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.description_text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.description_text.setStyleSheet(
            "QTextEdit { background: #2a2a2a; border: 1px solid #555; "
            "border-radius: 4px; padding: 8px; color: #e0e0e0; font-size: 13px; }"
        )
        self.description_text.document().setDocumentMargin(4)
        font_h = self.description_text.fontMetrics().height()
        self.description_text.setMinimumHeight(5 * font_h + 20)
        self.description_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        feedback_layout.addWidget(self.description_text, stretch=3)

        self.option_checkboxes = []
        if self.predefined_options and len(self.predefined_options) > 0:
            options_frame = QFrame()
            options_layout = QVBoxLayout(options_frame)
            options_layout.setContentsMargins(0, 10, 0, 10)

            for option in self.predefined_options:
                checkbox = QCheckBox(option)
                self.option_checkboxes.append(checkbox)
                options_layout.addWidget(checkbox)

            feedback_layout.addWidget(options_frame)

            separator = QFrame()
            separator.setFrameShape(QFrame.HLine)
            separator.setFrameShadow(QFrame.Sunken)
            feedback_layout.addWidget(separator)

        self.feedback_text = FeedbackTextEdit()
        self.feedback_text.image_pasted.connect(self._on_image_pasted)
        font_metrics = self.feedback_text.fontMetrics()
        row_height = font_metrics.height()
        padding = self.feedback_text.contentsMargins().top() + self.feedback_text.contentsMargins().bottom() + 5
        self.feedback_text.setMinimumHeight(5 * row_height + padding)
        self.feedback_text.setPlaceholderText("Enter your feedback here (Ctrl+Enter to submit, Ctrl+V to paste screenshot)")
        feedback_layout.addWidget(self.feedback_text, stretch=1)

        # --- Screenshot section ---
        screenshot_section = QFrame()
        screenshot_main_layout = QVBoxLayout(screenshot_section)
        screenshot_main_layout.setContentsMargins(0, 5, 0, 5)

        btn_layout = QHBoxLayout()
        capture_btn = QPushButton("📷 Capture Screen")
        capture_btn.setToolTip("Minimize this window and capture the full screen")
        capture_btn.clicked.connect(self._capture_screen)
        paste_btn = QPushButton("📋 Paste Clipboard")
        paste_btn.setToolTip("Paste an image from clipboard (you can also use Ctrl+V)")
        paste_btn.clicked.connect(self._paste_from_clipboard)
        browse_btn = QPushButton("📁 Browse...")
        browse_btn.setToolTip("Browse for image files")
        browse_btn.clicked.connect(self._browse_image)
        btn_layout.addWidget(capture_btn)
        btn_layout.addWidget(paste_btn)
        btn_layout.addWidget(browse_btn)
        btn_layout.addStretch()
        screenshot_main_layout.addLayout(btn_layout)

        self.screenshot_count_label = QLabel("")
        self.screenshot_count_label.setStyleSheet("color: #aaa; font-size: 12px;")
        self.screenshot_count_label.setVisible(False)
        screenshot_main_layout.addWidget(self.screenshot_count_label)

        self.screenshots_scroll = QScrollArea()
        self.screenshots_scroll.setWidgetResizable(True)
        self.screenshots_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.screenshots_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.screenshots_scroll.setFixedHeight(140)
        self.screenshots_scroll.setVisible(False)
        self.screenshots_scroll.setStyleSheet("QScrollArea { border: 1px solid #555; border-radius: 4px; }")

        self.thumbnails_container = QWidget()
        self.thumbnails_layout = QHBoxLayout(self.thumbnails_container)
        self.thumbnails_layout.setAlignment(Qt.AlignLeft)
        self.thumbnails_layout.setContentsMargins(4, 4, 4, 4)
        self.screenshots_scroll.setWidget(self.thumbnails_container)

        screenshot_main_layout.addWidget(self.screenshots_scroll)
        feedback_layout.addWidget(screenshot_section)

        submit_button = QPushButton("&Send Feedback")
        submit_button.clicked.connect(self._submit_feedback)
        feedback_layout.addWidget(submit_button)

        layout.addWidget(self.feedback_group)

    # --- Screenshot methods ---

    def _capture_screen(self):
        self.showMinimized()
        QTimer.singleShot(600, self._do_capture_screen)

    def _do_capture_screen(self):
        screen = QApplication.primaryScreen()
        if screen:
            pixmap = screen.grabWindow(0)
            if not pixmap.isNull():
                self._add_screenshot(pixmap)
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _paste_from_clipboard(self):
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime and mime.hasImage():
            image = clipboard.image()
            if not image.isNull():
                self._add_screenshot(QPixmap.fromImage(image))

    def _on_image_pasted(self, image: QImage):
        pixmap = QPixmap.fromImage(image)
        if not pixmap.isNull():
            self._add_screenshot(pixmap)

    def _browse_image(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "Select Images", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;All Files (*)"
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

        has_screenshots = len(self.screenshots) > 0
        self.screenshots_scroll.setVisible(has_screenshots)
        self.screenshot_count_label.setVisible(has_screenshots)
        if has_screenshots:
            self.screenshot_count_label.setText(f"{len(self.screenshots)} screenshot(s) attached")

    @staticmethod
    def _pixmap_to_base64(pixmap: QPixmap) -> str:
        byte_array = QByteArray()
        buffer = QBuffer(byte_array)
        buffer.open(QIODevice.WriteOnly)
        pixmap.save(buffer, "PNG")
        buffer.close()
        return byte_array.toBase64().data().decode('ascii')

    # --- Submit / Close ---

    def _submit_feedback(self):
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

        images_b64 = [self._pixmap_to_base64(p) for p in self.screenshots]

        self.feedback_result = FeedbackResult(
            interactive_feedback=final_feedback,
            images=images_b64,
        )
        self.close()

    def closeEvent(self, event):
        self.settings.beginGroup("MainWindow_General")
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        self.settings.endGroup()
        super().closeEvent(event)

    def run(self) -> FeedbackResult:
        self.show()
        QApplication.instance().exec()

        if not self.feedback_result:
            return FeedbackResult(interactive_feedback="", images=[])

        return self.feedback_result

def feedback_ui(prompt: str, predefined_options: list[str] | None = None, output_file: str | None = None, window_id: str = "0") -> FeedbackResult | None:
    app = QApplication.instance() or QApplication()
    app.setPalette(get_dark_mode_palette(app))
    app.setStyle("Fusion")
    ui = FeedbackUI(prompt, predefined_options, window_id=window_id)
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
    args = parser.parse_args()

    predefined_options = [opt for opt in args.predefined_options.split("|||") if opt] if args.predefined_options else None

    result = feedback_ui(args.prompt, predefined_options, args.output_file, window_id=args.window_id)
    if result:
        print(f"\nFeedback received:\n{result['interactive_feedback']}")
        if result.get('images'):
            print(f"Screenshots attached: {len(result['images'])}")
    sys.exit(0)
