# Class Diagram (v4.5 Explosive Speed)

```mermaid
classDiagram
    class Database {
        -MediaRepository media_repo
        -FaceRepository face_repo
        -SettingRepository settings_repo
        -DatabaseMigrationManager migration_manager
        +get_connection()
        +init_db()
        +sync_capture_dates()
    }

    class DatabaseMigrationManager {
        -string db_path
        +run_migrations()
        +sync_capture_dates()
        -_create_indices()
    }
    
    class FaceRepository {
        -string db_path
        +get_faces_by_category()
        +get_person_list_with_counts()
    }

    class MediaRepository {
        -string db_path
        +get_media_paged()
    }

    class SettingRepository {
        -string db_path
    }
    
    class FaceDisplayItem {
        +FaceInfo face
        +QImage image
        +bool selected
    }

    class MediaModel {
        -list[Any] data
        +rowCount()
        +data()
        +select_where(predicate: Callable)
        +update_face_image_batch()
    }

    class FaceManagerView {
        -ThumbnailGrid face_grid
        -MediaModel model
        +toggle_suggestion_mode()
        -_launch_suggestion_worker()
        -_on_suggestions_ready()
        -_apply_suggestion_grouping()
        -_request_missing_crops()
    }

    class ThumbnailGrid {
        -MediaModel media_model
        +mousePressEvent()
        +selection_changed(Signal)
    }

    FaceManagerView *-- MediaModel
    FaceManagerView *-- ThumbnailGrid
    ThumbnailGrid *-- MediaModel
    Database *-- DatabaseMigrationManager
    Database *-- MediaRepository
    Database *-- FaceRepository
    Database *-- SettingRepository
    
    MediaModel *-- FaceDisplayItem : "UI State"
    FaceManagerView --> FaceCropWorker : "Requests async crops"
    FaceManagerView --> FaceSuggestionWorker : "Similarity Search"
```

## Architectural Highlights (v4.5)
- **Concurrency & Safety**: `Database` provides `get_thread_local_connection()` to ensure safe multi-thread access. When combined with SQLite WAL mode, this design prevents `database is locked` errors during background processing.
- **Decoupled Lifecycle**: `Database` delegates all preparation (indexing, syncing) to `DatabaseMigrationManager`.
- **Predicate Selection**: `ThumbnailGrid` defines selection criteria via lambdas and injects them into `MediaModel.select_where()`.
- **Orchestrated UI**: `FaceManagerView` acts as a high-level orchestrator, decomposing complex result processing into specialized private helpers to maintain low cyclomatic complexity.
- **Model Implementation**: `MediaModel` is implemented in `ui.widgets.thumbnail_grid`, while data structures like `FaceDisplayItem` reside in `core.models`.
