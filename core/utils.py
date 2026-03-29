import os


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

