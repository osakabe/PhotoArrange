# PhotoArrange - Technical Specification

## 1. System Overview

PhotoArrange is a high-performance, AI-powered desktop application for organizing large-scale photo and video collections (16,000+ files). It automates media categorization using face recognition, duplicate detection, and geographical mapping while maintaining a responsive UI through advanced threading and lazy-rendering techniques.

*   **Platform**: Windows 10 / 11
*   **Target Hardware**: GPU (NVIDIA RTX 3060 Ti+) recommended for face analysis.
*   **Language**: Python 3.10+
*   **Environment**: Conda (Miniforge) `photo_env` recommended.

---

## 2. Technical Architecture

The application follows a modular architecture to separate UI, core data management, and AI processing.

### 2.1 Project Structure
- `main.py`: Entry point and UI controller.
- `core/`:
  - `database.py`: SQLite persistence layer.
  - `utils.py`: Common utility functions (app data directory management).
- `processor/`:
  - `image_processor.py`: EXIF metadata, Perceptual Hashing (ImageHash), thumbnails.
  - `face_processor.py`: Face detection (InsightFace), clustering (DBSCAN).
  - `geo_processor.py`: Reverse geocoding (GeoNames data + cKDTree).
- `ui/`:
  - `widgets/`: `MediaTreeView` (Lazy Loading), `ThumbnailGrid` (Infinite Scroll).
  - `dialogs/`: `SettingsDialog`.
  - `theme.py`: Custom CSS-based dark theme.

### 2.2 Threading Model
All heavy I/O and AI processing (scanning, hashing, inference, clustering) is handled by the `AnalysisWorker` (inheriting `QThread`).
- Communication via Qt Signals (`progress_val`, `phase_status`, `finished_all`).
- **Deferred Imports**: Major numerical libraries are imported **inside** the `run()` method to ensure the UI starts instantly even if DLL initialization is slow.

---

## 3. Core Features & Implementation

### 3.1 Face Recognition & Clustering
- **Inference**: Uses `InsightFace` (Buffalo_L model) with `onnxruntime`.
- **Clustering**: Faces are categorized using `DBSCAN` (scikit-learn) with a configurable cosine distance threshold.
- **Persistence**: 512-dim embeddings are stored as BLOBs in SQLite.

### 3.2 UI Scalability (16,000+ Files)
- **Lazy Loading (TreeView)**:
  - Year and Month nodes are expanded on demand using the `loadRequest` signal.
  - Improves initial boot time by 90% for large libraries.
- **Infinite Scrolling (ThumbnailGrid)**:
  - Thumbnails are rendered using a `QStyledItemDelegate` for high performance.
  - Pagination: Loads 50 items per "page". Automatically requests the next batch when the vertical scroll bar reaches 90% of its maximum.

### 3.3 Duplicate & Similarity Management
- **Detection**: Uses `ImageHash.phash` (Perceptual Hashing) to identify visually similar images across different resolutions and compression levels.
- **Grouping**:
  - Media with matching hashes are assigned to the "Duplicates" category.
  - The UI (ThumbnailGrid) inserts visual "Duplicate Group" headers between different hash groups.
  - **Sorting**: Within the Duplicates view, items are sorted by `image_hash` and then by `size` descending.
- **Smart Cleanup**:
  - A "Cleanup Duplicates" button provides automated organization.
  - **Policy**: The largest file (assumed highest quality) in each group is preserved. All other duplicates are safely moved to the OS **Recycle Bin** using `send2trash`.
  - Database consistency is maintained by purging records of deleted files.

### 3.4 Reverse Geocoding
- Uses `cities1000.txt` from GeoNames (167k+ cities).
- Employs `scipy.spatial.cKDTree` for ultra-fast nearest-neighbor lookup of coordinates (lat/lon).

---

## 4. Database Schema (SQLite)

### Table: `media`
| Column | Type | Description |
| :--- | :--- | :--- |
| `file_path` | TEXT (PK) | Absolute path to the file. |
| `last_modified` | REAL | File system timestamp for cache invalidation. |
| `metadata_json` | TEXT | JSON string of EXIF/video metadata. |
| `image_hash` | TEXT | Perceptual hash for duplicate detection. |
| `latitude` | REAL | GPS Latitude. |
| `longitude` | REAL | GPS Longitude. |
| `country` | TEXT | Reverse geocoded country name. |
| `year` | INTEGER | Extraction from EXIF `DateTaken`. |
| `month` | INTEGER | Extraction from EXIF `DateTaken`. |

### Table: `faces`
| Column | Type | Description |
| :--- | :--- | :--- |
| `face_id` | INTEGER (PK) | Auto-increment ID. |
| `file_path` | TEXT (FK) | Reference to `media`. |
| `vector_blob` | BLOB | 512-dimensional embedding (float32). |
| `cluster_id` | INTEGER | ID of the person cluster (result of DBSCAN). |
| `bbox_json` | TEXT | JSON coordinates of the face in the image. |

---

## 5. Windows Stabilization & DLL Management

To resolve silent startup crashes (Exit Code 1) common on Windows numerical environments:
- **DLL Injection**: `os.add_dll_directory` is used to explicitly add the Conda environment's `Library\bin` to the search path.
- **OpenMP Fix**: `os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"` is set before any imports to prevent `libiomp5md.dll` conflicts.
- **High-DPI**: `Qt.AA_EnableHighDpiScaling` attribute is enabled for modern displays.

---

## 6. Installation & Run

1. Create a Conda environment with Python 3.10.
2. Install dependencies: `pip install -r requirements.txt`.
3. Launch: `python main.py`.