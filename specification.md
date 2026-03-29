# PhotoArrange - Technical Specification (v3.2.0 AI-Native)

## 1. System Overview
PhotoArrange is an ultra-high-performance desktop application for organizing massive media collections using AI (DINOv2) and Vector Search (FAISS).

- **OS Target**: Windows 10/11
- **Architecture**: Normalized SQLite Database (V3.2) for maximum scalability (tested up to 1M items).

---

## 2. Technical Architecture (Normalized)

### 2.1 Database Schema (v3.2)
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
| `year` / `month` | INTEGER | Partitioning metadata |

#### Table: `duplicate_groups`
| Column | Type | Purpose |
| :--- | :--- | :--- |
| `group_id` | TEXT (PK) | Group Identifier (MD5 hex or 'sim:hash') |
| `discovery_method`| TEXT | 'exact' (MD5) or 'ai_local' (DINOv2) |

#### Table: `media_features` (Heavy Data)
| Column | Type | Purpose |
| :--- | :--- | :--- |
| `file_path` | TEXT (PK) | FK to `media.file_path` |
| `vector_blob`| BLOB | DINOv2 CLS Vector (384-dim float32) |
| `salient_blob`| BLOB | DINOv2 Salient Patches (64x384-dim float32) |

#### 2.4 Path Normalization Standard
Windows environments must adhere to a strict canonical path normalization to ensure database integrity and avoid duplicate detection misses.

- **Canonical Format**: `os.path.normcase(os.path.abspath(path))`
- **Enforcement**: Mandatory for all `INSERT`, `UPDATE`, and `SELECT` operations involving file paths. This ensures that `C:\Path\File.jpg` and `c:/path/file.jpg` are treated as the same entry.
- **Responsibility**: QA Sheriff audits all path-related code in `core/database.py` and `processor/duplicate_manager.py`.

---

## 3. AI Media Analysis Engine (v3.2.0)

### 3.1 Two-Stage Duplicate Discovery Pipeline
1.  **Stage 1: Global Similarity Search (FAISS)**:
    - **Method**: Radius Search on L2 Normalized DINOv2 Tensors.
    - **Threshold**: **0.6 L2 Distance** (~0.70 Cosine Similarity) for images, **0.4 L2** (~0.80) for videos.
    - **Video**: Uses 5-frame average vector for global matching.
2.  **Stage 2: Precision Verification (Salient Patch Matching)**:
    - **Method**: Localized patch-to-patch similarity using Stage 1 candidates.
    - **Threshold**: **0.80+ Correlation Score** (Restored to production standard).
    - **Throughput**: Batched GPU inference (4096 pairs per tick).

---

## 4. UI Features
- **Integrated Duplicate View**: Unified gallery displaying MD5 (Bit-accurate) and AI (Visually similar) duplicates in a single filtered view.
- **Fast Regrouping**: Database-backed re-clustering allowing threshold adjustments without re-scanning files.

---

## 5. Agent Roles & File Ownership
- **AI Architect**: `processor/feature_extractor.py`, `processor/image_processor.py`.
- **Data Librarian**: `core/database.py`, `processor/duplicate_manager.py`.
- **App Conductor**: `main.py`, `ui/`.
- **QA Sheriff**: `specification.md`, `.agent/rules/`.
