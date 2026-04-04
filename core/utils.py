import os
import ctypes
from ctypes import wintypes


def get_app_data_dir():
    if os.name == 'nt':
        # LocalAppData is best for cache/persistent data on Windows
        base_dir = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))
    else:
        # Linux/macOS fallback
        base_dir = os.path.expanduser('~')
    
    app_dir = os.path.join(base_dir, "PhotoArrange")
    if not os.path.exists(app_dir):
        os.makedirs(app_dir, exist_ok=True)
    return app_dir

def get_face_cache_dir():
    app_dir = get_app_data_dir()
    cache_dir = os.path.join(app_dir, ".face_cache")
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir, exist_ok=True)
    return cache_dir

def fix_dll_search_path():
    """
    Ensures that DLLs in the Conda environment (like CUDA, cuDNN, and FFmpeg)
    are discoverable on Windows. This fixes 0xC06D007F crashes.
    """
    if os.name == 'nt':
        import sys
        # 1. Add Library/bin (Conda's primary binary dir)
        env_bin = os.path.join(os.path.dirname(sys.executable), "Library", "bin")
        if os.path.exists(env_bin):
            try:
                os.add_dll_directory(env_bin)
            except: pass
            
        # 2. Add Library/lib (sometimes needed for older dlls)
        env_lib = os.path.join(os.path.dirname(sys.executable), "Library", "lib")
        if os.path.exists(env_lib):
            try:
                os.add_dll_directory(env_lib)
            except: pass
            
        # 3. Add current directory to PATH as fallback for older libraries
        os.environ["PATH"] = env_bin + os.pathsep + os.environ.get("PATH", "")


def get_short_path_name(long_name):
    """
    Returns the 8.3 short path name for a given long path.
    This is a workaround for old C++ libraries (like OpenCV VideoCapture)
    that do not support Unicode paths on Windows.
    """
    if os.name != 'nt':
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
    except:
        return long_name

