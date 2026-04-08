# PhotoArrange - Technical Specification (v4.5 Explosive Speed)

## 1. System Overview
PhotoArrange is an ultra-high-performance desktop application for organizing massive media collections using AI (DINOv2/InsightFace) and Vector Search (FAISS).

- **OS Target**: Windows 10/11
- **Performance Target**: Sub-100ms response for all metadata-heavy grid operations.
- **Architectural Integrity**: Strict adherence to SRP (Single Responsibility Principle) and Decoupled Modules.

---

## 2. Technical Architecture (v4.5 Decoupled)

### 2.1 Database & Denormalization
To bypass SQLite JOIN overhead (which can exceed 100s for 100k records), the system uses a **Denormalized-Local** strategy:
- **`capture_date`**: Mirrored from `media` to `faces` table.
- **`year` and `month`**: Extracted directly into the `media` table for fast grouping and sorting without runtime string parsing.
- **Explosive Index**: Uses composite index `idx_faces_explosive_sort` for instant filtered sorting.

### 2.2 Database Initialization & Lifecycle (NEW v4.5)
The initialization is decoupled via **`DatabaseMigrationManager`**:
1.  **Schema Definition**: Handles base table creation.
2.  **Versioning/Migration**: Manages column additions and data standardization (e.g. `cluster_id` defaults).
3.  **Performance Optimization**: Centralized management of complex indices.
4.  **Background Sync**: Heavy data migrations (Syncing capture dates) are handled asynchronously to ensure zero startup latency.

### 2.3 Repository Pattern & Type Safety
- **Facade Architecture**: Segregated into `MediaRepository`, `FaceRepository`, and `SettingRepository`.
- **Type-Safe DTOs**: All communication uses `@dataclass` structures in `core/models.py`.

---

## 3. Selection & Filtering Strategy (v4.5 Generic)

### 3.1 Predicate-Based Selection
The `MediaModel` implements a generic **`select_where(predicate)`** method:
- **Decoupling**: The Model does not know about specific UI concepts (suggestion labels, location tags).
- **Injection**: The View (UI layer) provides the selection logic via lambdas or predicate functions.
- **Complexity**: Eliminates massive `if/else` clusters, maintaining low cyclomatic complexity.

### 3.2 AI Face Management Engine
- **Centroid Matching**: Vectorized similarity search using FAISS/Numpy.
- **Orchestrated Suggestion Handlers**: Suggestions are processed via flattened, Early-Return based handlers (`_on_suggestions_ready`).

---

## 4. Quality & Engineering Standards

### 4.1 Cognitive Load & Complexity (Strict Rule)
- **Cyclomatic Complexity**: All methods must strive for complexity **<= 10**.
- **Early Returns**: Flatten nested logic to improve readability and testability.
- **SRP Enforcement**: Decouple UI state management from data loading workers.

### 4.2 Error Handling & Path Normalization
- **Strict Normalization**: All paths must use `os.path.normcase(os.path.abspath(path))` before comparison or DB entry.

### 4.3 Performance Monitoring & Latency Standard
- **Logging (PROFILER)**: Heavy operations must be measured using `time.perf_counter()` and logged in the format: `PROFILER: <処理名> took <時間>s`.
- **Latency Rule**: Total time from a user interaction (click) to the final UI reflection must be **within 3 seconds**.
- **Automated Testing**: Major GUI interactions must be covered by `pytest-qt` automated tests to continuously monitor and validate this latency standard.

---

## 5. Performance Metrics (v4.5 Verified)
- **DB Fetch (100k records)**: **< 0.1s** (via explosive indices).
- **UI Transition (Suggestion Mode)**: **< 0.05s** (No UI thread blocking).
- **Startup Time**: **Instant** (Heavy migrations offloaded to background).

---

## 6. Agent Roles & File Ownership
- **AI/CV Agent**: AI inference pipeline.
- **DB Agent**: Data persistence and Migration management.
- **Logic Agent**: Business logic, Model genericization, and lifecycle orchestration.
- **QA Sheriff**: Integrity, Standards and Metric validation.
- **UI Agent**: Layout, Event handlers, and UX visuals.

