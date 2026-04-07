# ER Diagram (v3.4.1 Performance Tuned)

```mermaid
erDiagram
    media ||--o{ faces : "contains detections"
    media {
        string file_path PK "Normalized (NOCASE)"
        real last_modified
        text metadata_json "JSON [Res, EXIF]"
        text group_id "FK (duplicate_groups)"
        string capture_date "ISO-8601"
        string file_hash "MD5 (NOCASE)"
        int is_corrupted
        int is_in_trash
    }

    faces {
        int face_id PK
        string file_path FK "idx_faces_file_path (Performance)"
        blob vector_blob "512-dim Embedding"
        text bbox_json "[x, y, w, h]"
        int cluster_id "FK (clusters) | idx_faces_cluster_id"
        int frame_index "0 for images, N for video"
        int is_ignored "Hidden/Rejected flag"
    }

    clusters ||--o{ faces : "groups"
    clusters {
        int cluster_id PK
        string custom_name
        int is_ignored "Cluster-wide hidden flag"
    }

    media ||--o| media_features : "features"
    media_features {
        string file_path PK
        blob vector_blob "DINOv2 Global"
        blob salient_blob "Localized Patches"
    }
```

## Performance Note (v3.4.1)
- **`idx_faces_file_path`**: Critical index added to `faces` table to fix O(N^2) JOIN latency between `faces` and `media`.
- **`idx_faces_cluster_id`**: Accelerates person-specific queries and AI suggestion candidate fetching.
