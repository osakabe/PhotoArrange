import time

from core.models import FaceDisplayItem
from ui.widgets.face_manager_view import FaceManagerView


def test_performance_category_load(qtbot, populated_db, repo):
    """Measure the time from category selection to data display (3s limit)."""
    view = FaceManagerView(populated_db, repo)
    qtbot.addWidget(view)
    view.show()

    # 1. Start timing
    start_time = time.perf_counter()

    # 2. Simulate selection of 'Unknown'
    view.load_faces(-1)

    # 3. Wait until loading is finished and items are rendered
    # We check for is_loading being False and at least one item in the model
    qtbot.waitUntil(
        lambda: not view.is_loading and len(view.face_grid.media_model._data) > 0, timeout=5000
    )

    end_time = time.perf_counter()
    duration = end_time - start_time

    print(f"PROFILER: Category Load took {duration:.4f}s")

    # Enforce the 3-second rule
    assert duration <= 3.0, f"Category loading took too long: {duration:.2f}s (Limit: 3.0s)"


def test_performance_sort_similarity(qtbot, populated_db, repo):
    """Measure the time to sort by similarity (3s limit)."""
    # Force a category selection with data first
    view = FaceManagerView(populated_db, repo)
    qtbot.addWidget(view)
    view.show()
    # Use a real person cluster from our fixture
    view.load_faces(100)
    qtbot.waitUntil(
        lambda: not view.is_loading and len(view.face_grid.media_model._data) > 0, timeout=3000
    )

    # Ensure items are there
    count = len([i for i in view.face_grid.media_model._data if isinstance(i, FaceDisplayItem)])
    assert count > 0

    # Start timing for the sort operation
    start_time = time.perf_counter()

    # Index 2 is "類似度 (高い順)" - this now triggers FaceSortWorker (async)
    view.sort_combo.setCurrentIndex(2)

    # Wait for the results to be calculated and applied
    def sort_finished():
        items = [x for x in view.face_grid.media_model._data if isinstance(x, FaceDisplayItem)]
        return any(getattr(i.face, "similarity", None) is not None for i in items)

    qtbot.waitUntil(sort_finished, timeout=5000)

    end_time = time.perf_counter()
    duration = end_time - start_time

    print(f"PROFILER: Similarity Sort for {count} items took {duration:.4f}s")

    # Enforce the 3-second rule
    assert duration <= 3.0, f"Similarity sort took too long: {duration:.2f}s (Limit: 3.0s)"
