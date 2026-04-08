from PySide6.QtCore import Qt

from core.models import MediaRecord
from ui.widgets.library_view import LibraryView


def test_library_view_structure(qtbot):
    """Test that LibraryView has the expected sub-widgets."""
    view = LibraryView()
    qtbot.addWidget(view)

    assert view.tree_view is not None
    assert view.grid_view is not None
    assert view.toolbar is not None


def test_library_view_grid_operations(qtbot):
    """Test data appending and clearing in the grid."""
    view = LibraryView()
    qtbot.addWidget(view)

    # Create dummy media records
    data = [MediaRecord(file_path=f"path_{i}.jpg", thumbnail_path="thumb.jpg") for i in range(10)]

    view.append_grid_data(data)
    # Wait for grid to update
    qtbot.waitUntil(lambda: view.grid_view.model().rowCount() > 0, timeout=2000)
    assert view.grid_view.model().rowCount() == 10

    view.clear_grid()
    assert view.grid_view.model().rowCount() == 0


def test_library_view_data_mapping(qtbot):
    """Test that MediaRecord fields are correctly mapped to displayable data."""
    view = LibraryView()
    qtbot.addWidget(view)

    from core.models import LibraryViewHeader, LibraryViewItem, MediaRecord

    # 1. Test Item mapping (Tags)
    data = [
        MediaRecord(
            file_path="test.jpg",
            person_tags="100:Alice,101:Bob",
            capture_date="2024:01:01 12:00:00",
        )
    ]
    view.append_grid_data([LibraryViewItem(media=data[0])])
    qtbot.waitUntil(lambda: view.grid_view.model().rowCount() > 0)

    item = view.grid_view.model().index(0, 0).data(Qt.UserRole)
    tags_raw = getattr(item.media, "person_tags")
    assert "Alice" in tags_raw
    assert "Bob" in tags_raw

    # 2. Test Header mapping (Formatting)
    header = LibraryViewHeader(date_header="2024-01-01", location_header="Tokyo")
    view.append_grid_data([header])

    header_item = view.grid_view.model().index(1, 0).data(Qt.UserRole)
    assert header_item.date_header == "2024-01-01"
    assert header_item.location_header == "Tokyo"
