"""
Central UI constants and theme definitions for PhotoArrange.
Follows the 'Premium UI' and 'Rich Aesthetics' guidelines from GEMINI.md.
"""

from PySide6.QtGui import QColor


class Theme:
    # Deep, elegant navy theme
    BACKGROUND = QColor("#0F111A")
    CARD_BG = QColor("#1A1D2E")
    CARD_HOVER = QColor("#24283D")
    BORDER = QColor("#2D324A")
    ACCENT = QColor("#3D5AFE")  # Primary Action Blue

    TEXT_PRIMARY = QColor("#FFFFFF")
    TEXT_SECONDARY = QColor("#8A8EA8")
    TEXT_MUTED = QColor("#5C617A")

    # Semantic Colors
    TRASH = QColor("#FF5252")  # Red for deletion/trash
    DUPLICATE = QColor("#F44336")  # Red badge for duplicates
    SUCCESS = QColor("#4CAF50")  # Green for completion
    HEADER_BG = QColor("#1F2336")


class Metrics:
    # Standard Grid Layout
    CARD_WIDTH = 180
    CARD_HEIGHT = 270
    CARD_SPACING = 5
    CORNER_RADIUS = 8

    THUMB_SIZE = 164
    MARGIN = 8

    # CROP Mode (AI Suggestions)
    CROP_CARD_WIDTH = 160
    CROP_CARD_HEIGHT = 190
    CROP_THUMB_SIZE = 144

    # Header
    HEADER_HEIGHT = 50
    HEADER_FONT_SIZE = 11
