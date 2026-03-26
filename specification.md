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
- `main.py`: Entry point and UI controller (Main Window logic).
- `core/`:
  - `database.py`: SQLite persistence layer (Thread-safe connections).
  - `utils.py`: Common utilities (Path management, system diagnostics).
- `processor/`:
  - `image_processor.py`: EXIF metadata, Perceptual Hashing (ImageHash), thumbnails.
  - `face_processor.py`: Face detection (InsightFace), clustering (DBSCAN).
  - `geo_processor.py`: Reverse geocoding (GeoNames data + cKDTree).
- `ui/`:
  - `widgets/`: `MediaTreeView` (Lazy Loading), `ThumbnailGrid` (Infinite Scroll).
  - `dialogs/`: `SettingsDialog`, `PersonManagerDialog` (Thumbnail face-cropping).
  - `theme.py`: Custom CSS-based dark theme (Glassmorphism inspired).

### 2.2 Threading Model
All heavy I/O and AI processing are handled by dedicated `QThread` subclasses:
- **`AnalysisWorker`**: Handles scanning, hashing, inference, and clustering.
- **`CleanupWorker`**: Handles bulk file deletions (`send2trash`) and database synchronization in the background to prevent UI freezing.
- **Communication**: Qt Signals (`progress_val`, `phase_status`, `finished_all`) ensure thread-safe UI updates.

---

## 3. Core Features & Implementation

### 3.1 Face Recognition & Clustering
- **Inference**: Uses `InsightFace` (Buffalo_L model) with `onnxruntime-gpu`.
- **Clustering**: Categorized using `DBSCAN` (scikit-learn) with a configurable cosine distance threshold (default: 0.5).
- **Optimization**: Face embeddings (512-dim) are stored as BLOBs. Clustering is performed as a final batch operation after all faces are detected.

### 3.2 UI Scalability (16,000+ Files)
- **Lazy Loading**: `MediaTreeView` uses on-demand node expansion to handle massive directory trees.
- **Pagination**: `ThumbnailGrid` loads 50 items per "page". Automatic "Infinite Scroll" triggers at 90% scroll height.
- **Performance Delegate**: Custom `QStyledItemDelegate` handles grid rendering without the overhead of individual QWidget items.

### 3.3 Duplicate & Similarity Management
- **Search Optimization**: Uses a single SQL `JOIN` query to identify all duplicate sets, eliminating the "N+1 query" performance bottleneck.
- **Database Indexing**: `idx_media_hash` on the `image_hash` column ensures sub-second retrieval from 10k+ records.
- **Smart Cleanup**: 
  - Preserves the largest file (highest quality).
  - Background Execution: Uses `CleanupWorker` for non-blocking Recycle Bin operations.

### 3.4 Person Management & Categorization
- **Manage People Dialog**: A centralized UI for naming identified person clusters.
- **Thumbnail face-cropping**:
  - **Memory Optimization**: Faces are cropped from 256px thumbnails rather than 10MB+ original images.
  - **Scaling Logic**: The source BBox coordinates [x1, y1, x2, y2] are scaled using `min(256/w, 256/h)` factor to match the thumbnail dimensions.
- **Ignore Functionality**: A boolean `is_ignored` flag in the database hides specific clusters (system misdetections or undesired people) from the UI.

---

## 4. Database Schema (SQLite)

### Table: `media`
- `file_path` (TEXT PK)
- `last_modified` (REAL)
- `metadata_json` (TEXT)
- `image_hash` (TEXT) - Indexed `idx_media_hash`
- `latitude` / `longitude` (REAL)
- `country` / `prefecture` / `city` (TEXT)
- `year` / `month` (INTEGER)

### Table: `faces`
- `face_id` (INTEGER PK)
- `file_path` (TEXT FK)
- `vector_blob` (BLOB)
- `cluster_id` (INTEGER)
- `bbox_json` (TEXT)

### Table: `clusters`
- `cluster_id` (INTEGER PK)
- `custom_name` (TEXT)
- `is_ignored` (INTEGER DEFAULT 0)

---

## 5. Windows Stabilization
- **DLL Management**: `os.add_dll_directory` points to the Conda `Library\bin` to prevent `Exit Code 1` crashes.
- **Path Normalization**: Uses `os.path.abspath(os.path.normpath)` for all `send2trash` calls to prevent `Errno 3` (Path not found).