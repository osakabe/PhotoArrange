import json
import os
import time
import numpy as np
import pytest
from PySide6.QtCore import Qt
from core.models import FaceDisplayItem
from ui.widgets.face_manager_view import FaceManagerView
from PIL import Image

@pytest.fixture
def large_populated_db(db, test_data_dir):
    """Populate the database with many faces for stress testing."""
    dummy_img_path = os.path.normcase(os.path.abspath(os.path.join(test_data_dir, "stress_dummy.jpg")))
    img = Image.new("RGB", (100, 100), color=(150, 150, 150))
    img.save(dummy_img_path, "JPEG")

    count = 2000  # Large enough to see scrolling performance
    faces = []

    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO media (file_path, file_hash, capture_date, is_corrupted) VALUES (?, ?, ?, 0)",
            (dummy_img_path, "stress_hash", "2024:01:01 00:00:00"),
        )
        for i in range(count):
            day = (i // 10) % 28 + 1
            date_str = f"2024:01:{day:02d} 12:00:00"
            vec = np.random.rand(512).astype(np.float32).tobytes()
            bbox = json.dumps([0, 0, 50, 50])
            cluster_id = 100 if i < 1000 else -1
            faces.append((dummy_img_path, vec, bbox, cluster_id, 0, date_str))

        conn.execute("INSERT INTO clusters (cluster_id, custom_name) VALUES (?, ?)", (100, "Stress Person"))
        conn.executemany(
            "INSERT INTO faces (file_path, vector_blob, bbox_json, cluster_id, is_ignored, capture_date) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            faces,
        )
        conn.commit()
    return db

def test_stress_scroll_and_load(qtbot, large_populated_db, repo):
    """Stress test for scrolling and incremental loading."""
    view = FaceManagerView(large_populated_db, repo)
    qtbot.addWidget(view)
    view.show()
    view.resize(1000, 800)

    # 1. Measure initial load
    start = time.perf_counter()
    view.load_faces(100) # Load Stress Person
    qtbot.waitUntil(lambda: not view.is_loading and len(view.face_grid.media_model._data) >= 100, timeout=5000)
    initial_load_time = time.perf_counter() - start
    print(f"PROFILER: Stress Initial Load (100 items) took {initial_load_time:.4f}s")
    assert initial_load_time <= 3.0

    # 2. Stress Scrolling
    # We scroll to trigger 'near_bottom_reached' multiple times
    print(f"Initial count: {len(view.face_grid.media_model._data)}")
    
    scroll_bar = view.face_grid.verticalScrollBar()
    
    # Scroll down in increments
    for i in range(5):
        current_count = len(view.face_grid.media_model._data)
        scroll_bar.setValue(scroll_bar.maximum())
        # Wait for next chunk
        qtbot.waitUntil(lambda: len(view.face_grid.media_model._data) > current_count, timeout=5000)
        print(f"Count after scroll {i+1}: {len(view.face_grid.media_model._data)}")

    final_count = len(view.face_grid.media_model._data)
    print(f"PROFILER: Stress Scroll Test finished with {final_count} items")
    assert final_count > 500

def test_suggestion_mode_performance(qtbot, large_populated_db, repo):
    """Measure performance of AI suggestion mode (mocked similarity)."""
    view = FaceManagerView(large_populated_db, repo)
    qtbot.addWidget(view)
    view.show()
    view.load_faces(100)
    qtbot.waitUntil(lambda: not view.is_loading, timeout=5000)

    # Toggle Suggestion Mode
    start = time.perf_counter()
    view.suggestion_btn.setChecked(True)
    view.toggle_suggestion_mode()
    
    # Wait for suggestions to be processed and displayed
    # Note: SuggestionWorker will take some time even if mocked because it queries DB
    qtbot.waitUntil(lambda: not view.is_loading and len(view.face_grid.media_model._data) > 0, timeout=10000)
    
    duration = time.perf_counter() - start
    print(f"PROFILER: Suggestion Mode Activation took {duration:.4f}s")
    assert duration <= 5.0 # AI can be a bit slower but 5s is a reasonable upper bound for UI reflection
