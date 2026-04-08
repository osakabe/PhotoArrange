import logging

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger("PhotoArrange")


class BaseWorker(QThread):
    """
    Standard base class for all background worker threads.
    Provides common signals and cancellation logic.
    """

    progress_val = Signal(int)
    phase_status = Signal(str)
    # Generic finished signal: (success, message)
    finished_task = Signal(bool, str)

    def __init__(self) -> None:
        super().__init__()
        self.is_cancelled: bool = False

    def stop(self) -> None:
        """Sets the cancellation flag and waits for the thread to stop."""
        self.is_cancelled = True
        logger.info(f"Stop requested for worker: {self.__class__.__name__}")

        # Rule 7: Cleanup GPU memory if possible when stopping AI-related workers
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def run(self) -> None:
        """To be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement run()")
