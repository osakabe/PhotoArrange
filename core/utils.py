import ctypes
import logging
import os
import shutil
import time
from ctypes import wintypes

logger = logging.getLogger("PhotoArrange")


class Profiler:
    """
    Standardized context manager for performance monitoring.
    Follows Project Rule: PROFILER: <task> took <time>s
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.start = 0.0

    def __enter__(self) -> "Profiler":
        self.start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        elapsed = time.perf_counter() - self.start
        import threading

        t_name = threading.current_thread().name
        logger.info(f"PROFILER: {self.name} took {elapsed:.4f}s [Thread: {t_name}]")


def get_app_data_dir() -> str:
    """
    Returns the application's data directory.
    Uses LocalAppData on Windows and the user's home directory on other systems.
    """
    if os.name == "nt":
        # LocalAppData is best for cache/persistent data on Windows
        base_dir = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    else:
        # Linux/macOS fallback
        base_dir = os.path.expanduser("~")

    app_dir = os.path.join(base_dir, "PhotoArrange")
    if not os.path.exists(app_dir):
        os.makedirs(app_dir, exist_ok=True)
    return app_dir


def get_face_cache_dir() -> str:
    """
    Returns the directory used for caching face crop images.
    """
    app_dir = get_app_data_dir()
    cache_dir = os.path.join(app_dir, ".face_cache")
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def fix_dll_search_path() -> None:
    """
    Ensures that DLLs in the Conda environment (like CUDA, cuDNN, and FFmpeg)
    are discoverable on Windows. This fixes 0xC06D007F crashes.
    """
    if os.name != "nt":
        return

    import sys

    # 1. Add Library/bin (Conda's primary binary dir)
    env_bin = os.path.join(os.path.dirname(sys.executable), "Library", "bin")
    if os.path.exists(env_bin):
        try:
            os.add_dll_directory(env_bin)
        except (OSError, AttributeError):
            pass

    # 2. Add Library/lib (sometimes needed for older dlls)
    env_lib = os.path.join(os.path.dirname(sys.executable), "Library", "lib")
    if os.path.exists(env_lib):
        try:
            os.add_dll_directory(env_lib)
        except (OSError, AttributeError):
            pass

    # 3. Add current directory to PATH as fallback for older libraries
    os.environ["PATH"] = env_bin + os.pathsep + os.environ.get("PATH", "")


def get_short_path_name(long_name: str) -> str:
    """
    Returns the 8.3 short path name for a given long path.
    This is a workaround for old C++ libraries (like OpenCV VideoCapture)
    that do not support Unicode paths on Windows.
    """
    if os.name != "nt":
        return long_name

    try:
        # Check if URL or something else that isn't a file
        if not os.path.exists(long_name):
            return long_name

        _GetShortPathNameW = ctypes.windll.kernel32.GetShortPathNameW
        _GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        _GetShortPathNameW.restype = wintypes.DWORD

        output_buf_size = 256
        while True:
            output_buf = ctypes.create_unicode_buffer(output_buf_size)
            needed = _GetShortPathNameW(long_name, output_buf, output_buf_size)
            if needed == 0:
                # Error (e.g. short names disabled on volume)
                return long_name
            if output_buf_size >= needed:
                return output_buf.value
            else:
                output_buf_size = needed
    except (OSError, AttributeError, ValueError):
        return long_name


def move_file_to_local_trash(file_path: str, root_folder: str) -> str:
    """
    Moves a file to a local '.trash' directory within the root folder.
    Also migrates its thumbnail so it doesn't lose its 'image' in the UI.
    Returns the new path of the file.
    """
    if not root_folder:
        return file_path

    trash_dir = os.path.join(root_folder, ".trash")
    try:
        if not os.path.exists(trash_dir):
            os.makedirs(trash_dir, exist_ok=True)

        base_name = os.path.basename(file_path)
        dest_path = os.path.join(trash_dir, base_name)

        # Handle filename collisions in trash
        if os.path.exists(dest_path):
            name, ext = os.path.splitext(base_name)
            counter = 1
            while os.path.exists(os.path.join(trash_dir, f"{name}_{counter}{ext}")):
                counter += 1
            dest_path = os.path.join(trash_dir, f"{name}_{counter}{ext}")

        if os.path.exists(file_path):
            shutil.move(file_path, dest_path)

            # --- THUMBNAIL MIGRATION ---
            # Also move the cached thumbnail to match the new path's hash
            # This ensures the "Trash" view still shows the image.
            from processor.image_processor import ImageProcessor

            img_proc = ImageProcessor()
            old_thumb = img_proc.get_thumbnail_path(file_path)
            new_thumb = img_proc.get_thumbnail_path(dest_path)

            if os.path.exists(old_thumb):
                # Ensure new thumb dir exists
                os.makedirs(os.path.dirname(new_thumb), exist_ok=True)
                shutil.move(old_thumb, new_thumb)

        return dest_path
    except Exception as e:
        logger.error(f"Error moving file to local trash: {e}")
        return file_path
