import logging
import os

logger = logging.getLogger("PhotoArrange")


def get_style_sheet() -> str:
    """
    Loads the global application stylesheet from ui/style.qss.
    Returns an empty string if the file cannot be found.
    """
    qss_path = os.path.join(os.path.dirname(__file__), "style.qss")
    if not os.path.exists(qss_path):
        logger.warning(f"Style sheet not found at {qss_path}")
        return ""

    try:
        with open(qss_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to load style sheet: {e}")
        return ""
