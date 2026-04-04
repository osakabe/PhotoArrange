# PhotoArrange - Technical Specification (v3.4.0 Robustness & Stability)

## 1. System Overview
PhotoArrange is an ultra-high-performance desktop application for organizing massive media collections using AI (DINOv2/InsightFace) and Vector Search (FAISS).

- **OS Target**: Windows 10/11
- **Architecture**: Normalized SQLite Database (V3.2+) for maximum scalability.
- **Reliability**: Strict thread lifecycle management and prioritized GPU resource handling.

---

## 2. Technical Architecture (Normalized)

### 2.1 Database Schema (v3.4)
The database is normalized into multiple tables to reduce I/O and memory overhead.

#### Table: `media` (Main Metadata)
| Column | Type | Purpose |
| :--- | :--- | :--- |
| `file_path` | TEXT (PK, NOCASE) | Normalized canonical path |
| `last_modified` | REAL | File mtime |
| `metadata_json` | TEXT | Resolution, EXIF tags |
| `group_id` | TEXT (NOCASE) | FK to `duplicate_groups.group_id` |
| `location_id` | INTEGER | FK to `locations.location_id` |
| `is_in_trash` | INT | Soft-delete flag |
| `capture_date` | TEXT | ISO format |
| `file_hash` | TEXT (NOCASE) | MD5 Checksum |

#### Table: `faces` (Face Detection)
| Column | Type | Purpose |
| :--- | :--- | :--- |
| `face_id` | INTEGER (PK) | Auto-increment ID |
| `file_path` | TEXT | FK to `media.file_path` |
| `vector_blob`| BLOB | InsightFace Embedding (512-dim float32) |
| `bbox_json` | TEXT | [x, y, w, h] of detection |
| `cluster_id` | INTEGER | FK to `clusters.cluster_id` |
| `frame_index`| INTEGER | Source frame (for videos) |
| `is_ignored` | INTEGER | Per-face ignore flag |

#### Table: `clusters` (Person Groups)
| Column | Type | Purpose |
| :--- | :--- | :--- |
| `cluster_id` | INTEGER (PK) | Auto-increment ID |
| `custom_name` | TEXT | User-defined name |
| `is_ignored` | INTEGER | Ignore flag (1=Hidden) |

#### Table: `ignored_person_vectors`
| Column | Type | Purpose |
| :--- | :--- | :--- |
| `id` | INTEGER (PK) | Auto-increment ID |
| `vector_blob`| BLOB | Representative vector of ignored person |

#### Table: `media_features` (Global Embeddings)
| Column | Type | Purpose |
| :--- | :--- | :--- |
| `file_path` | TEXT (PK) | Normalized canonical path |
| `vector_blob`| BLOB | DINOv2 Global Embedding |
| `salient_blob`| BLOB | Salient patch descriptors for precision matching |

#### 2.2 Path Normalization Standard
Windows environments must adhere to a strict canonical path normalization (`os.path.normcase(os.path.abspath(path))`). This is mandatory for all SQL operations to prevent case-sensitivity issues and duplicate records.

#### 2.3 SQL Concurrency
- **WAL Mode**: Enabled (`PRAGMA journal_mode=WAL`) for concurrent read/write.
- **Busy Timeout**: 5000ms to prevent "database is locked" errors during heavy AI processing.

---

## 3. AI Media Analysis Engine

### 3.1 Two-Stage Duplicate Discovery Pipeline
1.  **Stage 1: Global Similarity Search (FAISS)**: Radius Search on L2 Normalized DINOv2 Tensors.
2.  **Stage 2: Precision Verification (Salient Patch Matching)**: Localized patch-to-patch similarity (0.80+ threshold).

### 3.2 Face Recognition & Clustering Pipeline
1.  **Safe Initialization**: Prioritizes local project models (`/insightface`). Automatically detects and prefers `CUDAExecutionProvider` via ONNX Runtime, with robust `torch.cuda.is_available()` checks and CPU fallback.
2.  **Detection**: InsightFace `buffalo_l` (ResNet50). Optimized `det_size=(640, 640)` for speed/precision balance.
3.  **Embedding**: 512-dimensional facial feature extraction.
4.  **Clustering**: DBSCAN with `cosine` metric on normalized embeddings.
    - **EPS (Distance)**: 0.10 - 0.90 (Default: 0.42).
    - **Min Samples**: 1 - 20 (Default: 2).
5.  **VRAM Management**: Explicit `torch.cuda.empty_cache()` calls are performed after batch processing, inference cycles, and worker termination to prevent fragmentation and OOM.

---

## 4. UI & Thread Management

### 4.1 Non-Blocking UI
- All potentially long-running tasks (Scanning, AI Analysis, DB Resets) are offloaded to `QThread` workers.
- UI elements provide immediate feedback via `QProgressBar` and `QStatusBar`.

### 4.2 Thread Lifecycle & Safety
- **Worker termination**: Existing workers (e.g., `FaceRecognitionWorker`) are explicitly stopped and joined before starting new sessions or closing dialogs to prevent crashes.
- **Data Loading**: `DataLoaderWorker` uses termination/wait cycles to prevent parallel UI updates from the same grid.

### 4.3 Feature: Face Data Reset
- **Global Reset**: Empties `faces`, `clusters`, and `ignored_person_vectors` tables, resets auto-increment IDs, and clears VRAM.
- **Targeted Reset**: Clears face detection data for specific folder trees (maintains existing clusters).
- **Asynchronous Execution**: Handled via `FaceResetWorker` to keep the UI responsive.

---

## 5. Agent Roles & File Ownership
- **AI/CV Agent**: `processor/face_processor.py`, `processor/feature_extractor.py`, `processor/image_processor.py`.
- **DB Agent**: `core/database.py`, `processor/duplicate_manager.py`.
- **Logic Agent**: `core/sync_engine.py`, `main.py` (Worker coordination).
- **UI Agent**: `main.py`, `ui/`.
- **QA Sheriff**: `specification.md`, `.agent/rules/`, `tests/`.
