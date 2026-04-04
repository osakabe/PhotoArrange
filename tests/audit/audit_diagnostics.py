import sys
import os
import logging
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThread, Signal, QTimer
from PIL import Image
import cv2
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Audit")

class PriorityTestThread(QThread):
    def run(self):
        try:
            logger.info("Thread started. Attempting to set priority inside run()...")
            self.setPriority(QThread.LowPriority)
            logger.info("Successfully set priority inside run().")
        except Exception as e:
            logger.error(f"Error setting priority: {e}")

def test_priority():
    app = QApplication.instance() or QApplication(sys.argv)
    thread = PriorityTestThread()
    thread.finished.connect(app.quit)
    thread.start()
    QTimer.singleShot(1000, app.quit) # Timeout
    app.exec()

def test_opencv_fallback(dummy_path):
    # This checks if the current code would fail for a non-standard video extension
    is_video = dummy_path.lower().endswith(('.mp4', '.avi', '.mov', '.mkv'))
    logger.info(f"Path: {dummy_path}, is_video detection: {is_video}")
    
    if is_video:
        logger.info("Would use OpenCV path.")
    else:
        logger.info("Would use PIL path. This might fail if the file is actually a video.")

if __name__ == "__main__":
    logger.info("--- Testing QThread Priority ---")
    test_priority()
    logger.info("--- Testing Extension Coverage ---")
    test_opencv_fallback("video.m4v")
    test_opencv_fallback("video.webm")
