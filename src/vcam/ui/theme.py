"""라이트/다크 테마. 기본은 시스템 설정을 따른다."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from vcam.ui.tokens import (
    DARK,
    FONT_FALLBACK,
    FONT_FAMILY,
    LIGHT,
    RADIUS_CARD,
    RADIUS_CONTROL,
    Palette,
)


def resolve_palette(theme: str) -> Palette:
    if theme == "light":
        return LIGHT
    if theme == "dark":
        return DARK
    scheme = QGuiApplication.styleHints().colorScheme()
    return LIGHT if scheme == Qt.ColorScheme.Light else DARK


def apply_theme(app: QApplication, theme: str) -> Palette:
    p = resolve_palette(theme)
    app.setStyle("Fusion")
    font = QFont(FONT_FAMILY)
    font.setFamilies([FONT_FAMILY, FONT_FALLBACK, "Malgun Gothic"])
    font.setPointSizeF(9.5)
    app.setFont(font)

    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(p.bg))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(p.text))
    pal.setColor(QPalette.ColorRole.Base, QColor(p.surface))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(p.surface2))
    pal.setColor(QPalette.ColorRole.Text, QColor(p.text))
    pal.setColor(QPalette.ColorRole.Button, QColor(p.surface2))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(p.text))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(p.accent))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(p.accent_text))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(p.surface2))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(p.text))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(p.muted))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(p.muted))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(p))
    return p


def stylesheet(p: Palette) -> str:
    rc, rk = RADIUS_CONTROL, RADIUS_CARD
    return f"""
    QWidget {{ color: {p.text}; }}
    QMainWindow, QDialog {{ background: {p.bg}; }}
    QToolTip {{ background: {p.surface2}; color: {p.text}; border: 1px solid {p.border}; padding: 4px 8px; border-radius: {rc}px; }}

    QMenuBar {{ background: {p.bg}; padding: 2px 8px; }}
    QMenuBar::item {{ padding: 6px 10px; border-radius: {rc}px; background: transparent; }}
    QMenuBar::item:selected {{ background: {p.surface2}; }}
    QMenu {{ background: {p.surface}; border: 1px solid {p.border}; padding: 6px; border-radius: {rk}px; }}
    QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: {rc}px; }}
    QMenu::item:selected {{ background: {p.surface2}; }}
    QMenu::item:disabled {{ color: {p.muted}; }}
    QMenu::separator {{ height: 1px; background: {p.border}; margin: 6px 8px; }}
    QMenu::icon {{ padding-left: 10px; }}

    QFrame#card {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: {rk}px; }}
    QFrame#topBar {{ background: transparent; }}
    QFrame#bottomBar {{ background: {p.surface}; border-top: 1px solid {p.border}; }}
    QLabel[role="title"] {{ font-size: 13pt; font-weight: 600; }}
    QLabel[role="section"] {{ font-size: 10.5pt; font-weight: 600; }}
    QLabel[role="muted"] {{ color: {p.muted}; }}
    QLabel[role="value"] {{ font-weight: 600; }}
    QLabel[role="brand"] {{ font-size: 13pt; font-weight: 700; color: {p.text}; }}
    QLabel[role="badge"] {{ background: {p.surface2}; color: {p.muted}; border-radius: 9px; padding: 2px 8px; font-size: 8.5pt; }}
    QLabel[role="timer"] {{ font-size: 16pt; font-weight: 600; font-family: "Cascadia Mono", "Consolas"; }}

    QToolButton {{ background: transparent; border: none; border-radius: {rc}px; padding: 6px; }}
    QToolButton:hover {{ background: {p.surface2}; }}
    QToolButton:pressed {{ background: {p.border}; }}
    QToolButton:disabled {{ color: {p.muted}; }}
    QToolButton[role="mode"] {{ padding: 8px 14px; border: 1px solid transparent; font-weight: 600; }}
    QToolButton[role="mode"]:checked {{ background: {p.surface}; border: 1px solid {p.accent}; color: {p.accent}; }}
    QToolButton[role="pill"] {{ border: 1px solid {p.border}; padding: 6px 12px; }}
    QToolButton[role="pill"]:checked {{ border-color: {p.accent}; color: {p.accent}; }}

    QPushButton {{ background: {p.surface2}; border: 1px solid {p.border}; border-radius: {rc}px; padding: 7px 14px; }}
    QPushButton:hover {{ border-color: {p.accent}; }}
    QPushButton:disabled {{ color: {p.muted}; }}
    QPushButton[role="primary"] {{ background: {p.accent}; color: {p.accent_text}; border: none; font-weight: 600; }}
    QPushButton[role="primary"]:hover {{ background: {p.accent_hover}; }}
    QPushButton#recordButton {{ background: {p.accent}; color: {p.accent_text}; border: none; border-radius: 22px;
        padding: 10px 26px; font-size: 11pt; font-weight: 700; min-height: 24px; }}
    QPushButton#recordButton:hover {{ background: {p.accent_hover}; }}
    QPushButton#recordButton[recording="true"] {{ background: {p.rec}; }}
    QPushButton#recordButton:disabled {{ background: {p.border}; color: {p.muted}; }}
    QPushButton[role="round"] {{ border-radius: 22px; padding: 10px; min-width: 24px; min-height: 24px; }}

    QComboBox, QLineEdit, QSpinBox {{ background: {p.surface2}; border: 1px solid {p.border}; border-radius: {rc}px;
        padding: 5px 8px; min-height: 20px; selection-background-color: {p.accent}; }}
    QComboBox:hover, QLineEdit:hover, QSpinBox:hover {{ border-color: {p.accent}; }}
    QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{ border-color: {p.accent}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{ background: {p.surface}; border: 1px solid {p.border}; selection-background-color: {p.surface2};
        selection-color: {p.text}; outline: none; padding: 4px; }}
    QCheckBox {{ spacing: 8px; }}

    QListWidget {{ background: transparent; border: none; outline: none; }}
    QListWidget::item {{ border-radius: {rc}px; padding: 4px; margin: 1px 0; }}
    QListWidget::item:hover {{ background: {p.surface2}; }}
    QListWidget::item:selected {{ background: {p.surface2}; color: {p.text}; border: 1px solid {p.accent}; }}

    QTreeWidget {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: {rk}px; outline: none; }}
    QTreeWidget::item {{ padding: 4px; }}
    QHeaderView::section {{ background: {p.surface2}; border: none; padding: 6px; font-weight: 600; }}

    QStatusBar {{ background: {p.bg}; color: {p.muted}; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    """
