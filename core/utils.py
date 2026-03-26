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
