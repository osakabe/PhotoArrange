# ER Diagram (v4.5 Explosive Speed)

```mermaid
erDiagram
    media ||--o{ faces : "contains detections"
    locations ||--o{ media : "located at"
    duplicate_groups ||--o{ media : "grouped by"
    
    media {
        string file_path PK "Normalized (NOCASE)"
        real last_modified
        text metadata_json "JSON [Res, EXIF]"
        text group_id "FK (duplicate_groups)"
        int location_id "FK (locations)"
        string thumbnail_path
        int is_corrupted
        int is_in_trash
        string capture_date "ISO-8601 (Denormalization Source)"
        string file_hash "MD5 (NOCASE)"
        int year "Extracted for Grouping"
        int month "Extracted for Grouping"
    }

    faces {
        int face_id PK
        string file_path FK "idx_faces_path"
        blob vector_blob "512-dim Embedding"
        text bbox_json "[x, y, w, h]"
        int cluster_id "FK (clusters) | idx_faces_explosive_sort"
        int is_ignored "Hidden/Rejected flag"
        int frame_index "0 for images, N for video"
        string capture_date "Denormalized for Explosive Speed"
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

    locations {
        int location_id PK
        string country
        string prefecture
        string city
    }

    duplicate_groups {
        string group_id PK
        string discovery_method
    }

    settings {
        string key PK
        string value
    }
```

## Performance & Optimization Notes (v4.5)
- **Denormalized `faces.capture_date`**: Essential for sub-100ms sorting. By duplicating the media date into the faces table, we eliminate cross-table JOINs during filtered grid loading.
- **`idx_faces_explosive_sort`**: A composite index `(is_ignored, cluster_id, capture_date DESC)`. This index allows the SQLite engine to perform filtering and sorting in a single indexed operation, reducing latency from 100s to <100ms.
- **Index `idx_faces_path`**: Critical for joining with media-related metadata when explicitly required (e.g. detailed EXIF display).
