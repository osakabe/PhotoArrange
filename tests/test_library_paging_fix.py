import time

from core.models import LibraryViewItem
from ui.widgets.library_view import LibraryView


def test_library_paging_uniqueness_and_performance(qtbot, populated_db):
    """
    GUI Test: Verify that selecting a person in LibraryView
    displays unique items and meets the 3-second rule.
    """
    view = LibraryView()
    qtbot.addWidget(view)
    view.show()

    # 0. The populated_db (from conftest) contains 500 faces.
    # 50 of them belong to Person 100, and they all point to THE SAME FILE path.
    # BEFORE FIX: This would show 50 items in the grid.
    # AFTER FIX: This should show 1 item in the grid.

    # Use the database from the fixture
    view.db = populated_db

    # 1. Start timing
    start_time = time.perf_counter()

    # 2. Simulate data loading for Person 100
    # In the app, DataLoaderWorker calls media_repo.get_media_paged
    results = view.db.media_repo.get_media_paged(
        cluster_id=100, year=None, month=None, limit=50, include_trash=False
    )

    # 3. Update GUI
    # We map results to LibraryViewItem
    display_items = [LibraryViewItem(media=m) for m in results]
    view.append_grid_data(display_items)

    # 4. Wait until items are rendered
    qtbot.waitUntil(lambda: len(view.grid_view.model()._data) > 0, timeout=5000)

    end_time = time.perf_counter()
    duration = end_time - start_time

    print(f"PROFILER: Library Paging (Person 100) took {duration:.4f}s")

    # 5. Enforce uniqueness: Only 1 item should be returned by the repo
    # because all 50 faces in the fixture belong to the same file.
    assert len(results) == 1, (
        f"Expected 1 unique file, but found {len(results)}. Duplication fix failed!"
    )
    assert len(view.grid_view.model()._data) == 1, "GUI displayed more than 1 item!"

    # 5. Enforce 3-second rule
    assert duration <= 3.0, f"Library paging took too long: {duration:.2f}s"

    # 6. Verify Tag uniqueness
    tags = results[0].person_tags
    assert "100:Person 100" in tags


def test_tree_expansion_async(qtbot, populated_db):
    """
    Test that TreeDataLoadWorker correctly fetches years and
    MediaTreeView.add_sub_items handles dataclasses without crashing.
    """
    from core.models import YearCount
    from processor.workers import TreeDataLoadWorker
    from ui.widgets.tree_view import MediaTreeView

    tree = MediaTreeView()
    qtbot.addWidget(tree)
    root_item = tree.add_category_node("Person 100", 100)

    # Pre-populate year column for the dummy media in populated_db
    with populated_db.get_connection() as conn:
        conn.execute("UPDATE media SET year = 2024, month = 1")
        conn.commit()

    # 1. Start worker
    worker = TreeDataLoadWorker(populated_db, root_item, "years", {"cluster_id": 100})

    # 2. Track result
    loaded_res = []
    worker.data_ready.connect(lambda res: loaded_res.append(res))

    with qtbot.waitSignal(worker.data_ready, timeout=5000):
        worker.run()  # Run synchronously in test for simplicity, or use thread

    res = loaded_res[0]
    assert res.success
    assert len(res.data) > 0
    assert isinstance(res.data[0], YearCount)

    # 3. Verify GUI update (the part that previously crashed)
    tree.add_sub_items(res.item, res.data, res.level)

    # Check that children were added
    assert root_item.rowCount() > 0
    # Child 0 is "Loading...", Child 1+ are the years
    # Wait, add_sub_items appends. In on_item_expanded, "Loading..." is removed.
    year_item = root_item.child(1)  # Child 0 was "Loading..."
    assert "2024" in year_item.text()
