from core.models import ClusterInfo, FaceInfo, MediaRecord


def test_media_record_from_media_table():
    row = (
        "C:\\photos\\img1.jpg",
        1600000000.0,
        '{"width": 800}',
        "group_1",
        1,
        "thumb_img1.jpg",
        0,
        0,
        "2024-01-01",
        "hash1",
        2024,
        1,
    )
    m = MediaRecord.from_media_table(row)
    assert m.file_path == "C:\\photos\\img1.jpg"
    assert m.metadata["width"] == 800
    assert m.group_id == "group_1"
    assert m.year == 2024
    assert m.month == 1


def test_media_record_from_full_join():
    row = (
        "C:\\photos\\img2.jpg",
        1600000000.0,
        '{"width": 800}',
        "group_2",
        0,
        0,
        0,
        "Japan",
        "Tokyo",
        "Shibuya",
        "2024",
        "02",
        "thumb_img2.jpg",
        0,
        0,
        "2024-02-01",
        "hash2",
        b"fake_blob",
    )
    m = MediaRecord.from_full_join(row)
    assert m.file_path == "C:\\photos\\img2.jpg"
    assert m.country == "Japan"
    assert m.city == "Shibuya"
    assert m.year == 2024
    assert m.month == 2
    assert m.vector_blob == b"fake_blob"


def test_media_record_from_duplicate_search():
    row = (
        "C:\\photos\\img3.jpg",
        "group_3",
        '{"width": 800}',
        0,
        "hash3",
        "2024-03-01",
        b"vector",
        b"salient",
        "ai_local",
    )
    m = MediaRecord.from_duplicate_search(row)
    assert m.file_path == "C:\\photos\\img3.jpg"
    assert m.discovery_method == "ai_local"
    assert m.vector_blob == b"vector"
    assert m.salient_blob == b"salient"


def test_face_info_from_db_row():
    row = (10, "C:\\photos\\img1.jpg", "[10.0, 20.0, 30.0, 40.0]", 5, 0, "2024-01-01", 0)
    f = FaceInfo.from_db_row(row)
    assert f.face_id == 10
    assert f.file_path == "C:\\photos\\img1.jpg"
    assert f.bbox == [10.0, 20.0, 30.0, 40.0]
    assert f.cluster_id == 5
    assert f.is_ignored is False


def test_cluster_info_from_cluster_row():
    row = (1, "Alice", 0, 15)
    c = ClusterInfo.from_cluster_row(row)
    assert c.cluster_id == 1
    assert c.custom_name == "Alice"
    assert c.is_ignored is False
    assert c.face_count == 15
