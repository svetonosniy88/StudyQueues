"""Palette shared by the daily workspace and its native Qt dialogs."""
from pathlib import Path
from PySide6.QtGui import QColor, QPalette
from .ui import apply_theme

BG = '#1b191e'
TEXT = '#f0eaf4'
MUTED = '#b6aaba'
ACCENT = '#b78be8'


def apply_workspace_theme(app):
    if 'QLabel#largeClock' in app.styleSheet():
        return
    apply_theme(app)  # Register static Segoe UI for offscreen QA as well.
    palette = QPalette()
    colors = {
        QPalette.Window: BG, QPalette.Base: '#242128', QPalette.AlternateBase: '#2c2831',
        QPalette.Text: TEXT, QPalette.WindowText: TEXT, QPalette.Button: '#302936',
        QPalette.ButtonText: TEXT, QPalette.Highlight: '#49335f',
        QPalette.HighlightedText: '#f4e8ff', QPalette.PlaceholderText: MUTED,
        QPalette.ToolTipBase: '#302936', QPalette.ToolTipText: TEXT,
        QPalette.Link: ACCENT, QPalette.Light: '#514657', QPalette.Mid: '#3a343f',
        QPalette.Dark: '#151218',
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor('#82778b'))
    app.setPalette(palette)
    check = Path(__file__).with_name('check.svg').as_posix()
    style = Path(__file__).with_name('workspace_dark.qss').read_text(encoding='utf-8')
    style = style.replace('@chevron', Path(__file__).with_name('chevron-down.svg').as_posix())
    app.setStyleSheet(style + f'\nQCheckBox::indicator:checked {{ image: url("{check}"); }}')
