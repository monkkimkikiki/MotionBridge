from PySide6.QtGui import QColor, QPalette

ACCENT = "#5b7cfa"

STYLE = """
* { font-family: "Malgun Gothic", "맑은 고딕", "Segoe UI", sans-serif; font-size: 10pt; }
QMainWindow, QDialog { background: #15171c; }
QWidget { color: #e6e8ee; }
QWidget#page { background: #15171c; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; color: #8b93a7; padding: 10px 26px; font-size: 11pt;
               font-weight: 600; border-bottom: 3px solid transparent; }
QTabBar::tab:selected { color: #ffffff; border-bottom: 3px solid #7c9cff; }
QTabBar::tab:hover { color: #d0d6e4; }
QFrame#header { background: #1a1d23; border-bottom: 1px solid #262a33; }
QLabel#appTitle { font-size: 15pt; font-weight: 800; }
QFrame#card { background: #1d2027; border: 1px solid #2a2e38; border-radius: 12px; }
QLabel#cardTitle { font-size: 13pt; font-weight: 700; }
QLabel#muted { color: #8b93a7; }
QLabel#big { font-size: 22pt; }
QLabel#statusOk { color: #46c37b; font-weight: 600; }
QLabel#statusBad { color: #f0a03c; font-weight: 600; }
QLabel#rec { color: #ff5c5c; font-weight: 700; font-size: 12pt; }
QPushButton { background: #2a2e38; border: 1px solid #353a46; border-radius: 8px; padding: 7px 14px; }
QPushButton:hover { background: #333846; }
QPushButton:pressed { background: #262a33; }
QPushButton:disabled { color: #5d6476; background: #1f2229; border-color: #2a2e38; }
QPushButton#primary { background: #5b7cfa; border: none; color: white; font-weight: 700; font-size: 12pt; padding: 12px 22px; }
QPushButton#primary:hover { background: #6d8bff; }
QPushButton#primary:disabled { background: #2d3550; color: #7f88a6; }
QPushButton#danger { background: #e5484d; border: none; color: white; font-weight: 700; font-size: 12pt; padding: 12px 22px; }
QPushButton#danger:hover { background: #f25d61; }
QPushButton#link { background: transparent; border: none; color: #8fa6ff; padding: 2px 4px; text-decoration: underline; }
QFrame#drop { border: 2px dashed #3a4050; border-radius: 12px; background: #181a20; }
QFrame#drop:hover { border-color: #56607a; }
QFrame#drop[active="true"] { border-color: #7c9cff; background: #1c2233; }
QListView, QListWidget, QPlainTextEdit, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTableWidget {
    background: #181a20; border: 1px solid #2a2e38; border-radius: 8px; padding: 4px;
    selection-background-color: #34437a; selection-color: #ffffff; }
QComboBox { padding: 5px 8px; min-height: 22px; }
QComboBox QAbstractItemView { background: #1d2027; border: 1px solid #2a2e38; }
QLineEdit, QSpinBox, QDoubleSpinBox { padding: 5px 8px; }
QListWidget::item { padding: 6px 8px; border-radius: 6px; }
QListWidget::item:selected { background: #2f3b66; }
QListView#grid::item { border-radius: 8px; padding: 6px; }
QListView#grid::item:selected { background: #2f3b66; }
QListView#grid::item:hover { background: #232733; }
QProgressBar { background: #181a20; border: 1px solid #2a2e38; border-radius: 6px; min-height: 14px; max-height: 14px; text-align: center; font-size: 8pt; }
QProgressBar::chunk { background: #5b7cfa; border-radius: 5px; }
QSlider::groove:horizontal { height: 6px; background: #2a2e38; border-radius: 3px; }
QSlider::handle:horizontal { background: #9db1ff; width: 16px; margin: -6px 0; border-radius: 8px; }
QSlider::sub-page:horizontal { background: #5b7cfa; border-radius: 3px; }
QGroupBox { border: 1px solid #2a2e38; border-radius: 10px; margin-top: 16px; padding: 14px 12px 12px 12px; font-weight: 700; background: #1a1d23; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #c9d1e6; }
QHeaderView::section { background: #1d2027; border: none; padding: 6px; color: #aab2c5; }
QTableWidget { gridline-color: #2a2e38; }
QCheckBox { spacing: 8px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar::handle:vertical { background: #333846; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QFrame#taskPanel { background: #1a1d23; border-top: 1px solid #262a33; }
QToolTip { background: #2a2e38; color: #fff; border: 1px solid #3a4050; padding: 4px; }
QMenu { background: #1d2027; border: 1px solid #2a2e38; padding: 4px; }
QMenu::item { padding: 6px 20px; border-radius: 4px; }
QMenu::item:selected { background: #2f3b66; }
"""


def palette():
    pal = QPalette()
    for role, color in [
        (QPalette.ColorRole.Window, "#15171c"), (QPalette.ColorRole.WindowText, "#e6e8ee"),
        (QPalette.ColorRole.Base, "#181a20"), (QPalette.ColorRole.AlternateBase, "#1d2027"),
        (QPalette.ColorRole.Text, "#e6e8ee"), (QPalette.ColorRole.Button, "#2a2e38"),
        (QPalette.ColorRole.ButtonText, "#e6e8ee"), (QPalette.ColorRole.Highlight, "#34437a"),
        (QPalette.ColorRole.HighlightedText, "#ffffff"), (QPalette.ColorRole.ToolTipBase, "#2a2e38"),
        (QPalette.ColorRole.ToolTipText, "#ffffff"), (QPalette.ColorRole.PlaceholderText, "#6b7387"),
    ]:
        pal.setColor(role, QColor(color))
    return pal


def apply(app):
    app.setStyle("Fusion")
    app.setPalette(palette())
    app.setStyleSheet(STYLE)


def apply_to(widget):
    """프로그램 전체가 아니라 이 화면(과 그 안)에만 AnimRef 모양을 입힌다 (MotionBridge 안에 탭으로 넣을 때)."""
    widget.setPalette(palette())
    widget.setStyleSheet(STYLE)
