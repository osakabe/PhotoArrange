# PhotoArrange - Technical Specification (v4.0 Async & Type-Safe Arch)

## 1. System Overview
PhotoArrange is an ultra-high-performance desktop application for organizing massive media collections using AI (DINOv2/InsightFace) and Vector Search (FAISS).

- **OS Target**: Windows 10/11
- **Architecture**: Normalized SQLite Database (V3.2+) for maximum scalability.
- **Reliability**: Strict thread lifecycle management and prioritized GPU resource handling.
- **Asynchronous First**: All heavy I/O, AI inference, and file operations are offloaded to background workers to ensure zero UI freezing.

---

## 2. Technical Architecture (Modernized)

### 2.1 Database Schema (v3.5)
The database is normalized into multiple tables to reduce I/O and memory overhead.
- **Indices**: Optimized B-Tree indexes on `is_in_trash`, `group_id`, `capture_date`, and `cluster_id`.
- **Bulk Operations**: High-volume updates (media deletion, face association) use `executemany` batch SQL transactions for atomic and fast execution.

### 2.2 SQL Concurrency
- **WAL Mode**: Enabled (`PRAGMA journal_mode=WAL`) for concurrent read/write.
- **Busy Timeout**: 5000ms to prevent "database is locked" errors.

### 2.3 Repository Pattern & Full Type Safety (v4.0)
- **Facade Architecture**: Raw SQL interactions are segregated into `MediaRepository`, `FaceRepository`, and `SettingRepository`. The `Database` class acts as a facade, delegating to specialized repositories.
- **Type-Safe Data Transfer Objects (DTOs)**: 
  - Raw dictionary passing (`dict.get()`) is deprecated.
  - All layers communicate via immutable-style `@dataclass` structures in `core/models.py`.
  - Core models include: `MediaRecord`, `FaceInfo`, `LibraryViewItem`, `FaceDisplayItem`, `LibraryViewHeader`, and `FaceCountsResult`.
- **Signal-Based Communication**: Worker logic communicates with the UI strictly via validated DataClass signals, ensuring type safety across thread bounds and preventing main-thread blocking.

---

## 3. AI Face Management Engine (v3.0 Overhaul)

### 3.1 Adaptive AI Suggestion Mode
The system provides a non-blocking suggestion pipeline:
1.  **Centroid Calculation**: Calculates average embedding for a target person.
2.  **Vectorized Matching**: Uses optimized Numpy operations for similarity search.
3.  **Non-Blocking UI**: Suggestions are loaded incrementally by `FaceSuggestionWorker` and processed into `FaceDisplayItem` objects without freezing the main thread.
4.  **Background Crop Generation**: `FaceCropWorker` generates missing face thumbnails in the background, updating the UI dynamically as they become available.

---

## 4. UI & Thread Management

### 4.1 BaseWorker & Thread Lifecycles
All background tasks inherit from a standardized `BaseWorker` (QThread):
- **Non-Waiting Lifecycle**: Use of `QThread.wait()` is prohibited. Threads signal their completion and are managed by a centralized worker tracker in UI components.
- **Incremental Data Arrival**: Results are delivered in chunks (e.g., `chunk_ready` signals) for immediate user feedback.

### 4.2 Unified Rendering (ThumbnailGrid)
- **Polymorphic Grid**: A single `ThumbnailGrid` component handles both general media (Library) and face crops (Faces) by supporting multiple DataClass types.
- **Library-Style Grouping**: Specialized `LibraryViewHeader` objects provide premium, date-based grouping with sticky behavior.

---

## 5. Performance Metrics (v4.0 Audit)
- **DB Fetch (10k items)**: <0.1s (via specialized indices and batch selection).
- **Sidebar Loading**: <0.2s for full counts and person lists using optimized conditional aggregation.
- **UI Responsiveness**: Constant 60fps interaction during background heavy-load tasks.

---

## 6. Agent Roles & File Ownership
- **AI/CV Agent**: AI pipeline (`processor/face_processor.py`, `processor/feature_extractor.py`).
- **DB Agent**: Data persistence (`core/database.py`, `core/repositories/*.py`).
- **Logic Agent**: Business flow and Workers (`core/sync_engine.py`, `processor/workers.py`).
- **QA Sheriff**: Integrity and Standards (`specification.md`, `.agent/rules/`).
- **UI Agent**: Visuals and UX (`main.py`, `ui/widgets/*`).

