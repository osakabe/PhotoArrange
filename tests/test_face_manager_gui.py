from ui.widgets.face_manager_view import FaceManagerView


def test_face_manager_initial_load(qtbot, populated_db, repo, mocker):
    """Verify that FaceManagerView loads faces and categories correctly from the DB."""
    # Mock face cache to avoid OS-level file issues if possible,
    # but here we rely on the populated fixture's dummy image

    view = FaceManagerView(populated_db, repo)
    qtbot.addWidget(view)
    view.show()

    view.refresh_sidebar()

    # 1. Check sidebar categories
    # The sidebar loads asynchronously via SidebarLoadWorker
    def check_sidebar():
        # defaults are Unknown and Ignored, so rowCount() should reach 2+ quickly
        return view.sidebar.model.rowCount() >= 2

    qtbot.waitUntil(check_sidebar, timeout=5000)

    # "❓ 不明" should be the first item
    item_text = view.sidebar.model.item(0).text()
    assert "不明" in item_text

    # 2. Check initial face loading (should load Unknown by default or after selection)
    # Typically load_faces(-1) is called on startup or after sidebar load
    # In current implementation, we might need to click or force it
    view.load_faces(-1)

    def check_grid_loaded():
        # Check if the grid has items. Note: grid_container is where the items are
        return view.face_grid.media_model.rowCount() > 0

    qtbot.waitUntil(check_grid_loaded, timeout=5000)

    # Verify counts
    data_count = len(view.face_grid.media_model._data)
    # Filter for FaceDisplayItem specifically if headers are present
    from core.models import FaceDisplayItem

    face_items = [i for i in view.face_grid.media_model._data if isinstance(i, FaceDisplayItem)]
    assert len(face_items) > 0
    print(f"Loaded {len(face_items)} faces into the grid.")


def test_face_manager_category_switching(qtbot, populated_db, repo):
    """Verify that clicking the sidebar switches face categories."""
    view = FaceManagerView(populated_db, repo)
    qtbot.addWidget(view)
    view.show()

    view.refresh_sidebar()

    # Wait for sidebar
    qtbot.waitUntil(lambda: view.sidebar.model.rowCount() >= 2, timeout=2000)

    # Click "🚫 無視" (usually index 1 if it exists)
    # We'll just call the slot directly to avoid complex tree interaction in basic test
    view.load_faces(-2)  # -2 is IGNORED

    # Current grid should clear and reload
    # (In our fixture, we only put faces in cluster_id -1, so -2 should be empty)
    qtbot.waitUntil(lambda: not view.is_loading, timeout=2000)

    from core.models import FaceDisplayItem

    face_items = [i for i in view.face_grid.media_model._data if isinstance(i, FaceDisplayItem)]
    assert len(face_items) == 0  # Expected empty for 'Ignored' category in this fixture
