# Sequence Diagrams (v4.5 Explosive Speed)

## 1. Application Startup & Initialization
This sequence demonstrates how heavy database synchronization is offloaded to ensure an instant UI appearance.

```mermaid
sequenceDiagram
    participant U as User
    participant M as MainWindow
    participant DB as Database
    participant MM as MigrationManager
    participant W as DatabaseSyncWorker

    U->>M: Launch App
    M->>DB: Instantiate
    DB->>MM: Instantiate
    DB->>MM: run_migrations()
    MM-->>DB: Indices & Schema Ready
    M->>M: init_ui()
    M-->>U: Show Window (Instant)
    
    Note over M, W: Background Maintenance
    M->>W: Start(db)
    W->>DB: sync_capture_dates()
    DB->>MM: sync_capture_dates()
    MM->>MM: SQL: UPDATE faces SET capture_date...
    MM-->>W: Sync Complete
    W-->>M: Signal(finished)
```

## 2. Explosive Face Loading (Single Table Lookup)
Demonstrates sub-100ms loading by bypassing expensive JOINs and leveraging the explosive sort index.

```mermaid
sequenceDiagram
    participant V as FaceManagerView
    participant W as FaceLoadWorker
    participant R as FaceRepository
    participant DB as SQLite (idx_faces_explosive_sort)
    participant Model as MediaModel

    V->>W: start(category_id)
    W->>R: get_faces_by_category()
    R->>DB: SELECT * FROM faces WHERE ... ORDER BY capture_date DESC
    Note right of DB: Single-table index lookup (<100ms)
    DB-->>R: list[FaceInfo]
    R-->>W: list[FaceInfo]
    W-->>V: Signal(faces_ready)
    V->>Model: append_data(display_items)
    Model-->>V: dataChanged()
```

## 3. Orchestrated AI Suggestions
Demonstrates the flattened processing flow in the UI layer.

```mermaid
sequenceDiagram
    participant V as FaceManagerView
    participant W as FaceSuggestionWorker
    participant Logic as SuggestionLogic
    participant Model as MediaModel
    participant Crop as FaceCropWorker

    V->>W: start(person_id)
    W->>Logic: calculate_similarities()
    Logic-->>W: list[matches]
    W->>V: suggestions_ready(list)
    
    V->>V: _should_ignore_suggestions()
    V->>V: _build_display_items()
    V->>V: _apply_suggestion_grouping()
    V->>Model: append_data()
    V->>Crop: start(faces)
    
    Crop-->>Model: update_face_image_batch()
    Model-->>V: dataChanged()
```
