import sys
import os

try:
    from ui.dialogs.settings_dialog import SettingsDialog
    print("SettingsDialog import OK")
except Exception as e:
    print(f"SettingsDialog import FAILED: {e}")
    import traceback
    traceback.print_exc()

try:
    # main.py might have side effects on import, so be careful
    # But usually syntax errors are caught first
    import main
    print("main.py import OK")
except SyntaxError as se:
    print(f"main.py SyntaxError: {se}")
except Exception as e:
    print(f"main.py import FAILED (possibly side effects): {e}")
