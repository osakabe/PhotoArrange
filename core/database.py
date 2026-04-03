import sqlite3
import json
import os
import logging
import numpy as np
from .utils import get_app_data_dir

logger = logging.getLogger("PhotoArrange")

class Database:
    def __init__(self, db_path=None):
        if db_path is None:
            self.db_path = os.path.join(get_app_data_dir(), "media_cache.db")
        else:
            self.db_path = db_path
        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def init_db(self):
        with self.get_connection() as conn:
            cursor = conn.execute('PRAGMA user_version')
            version = cursor.fetchone()[0]
            
            # Check if we need to migrate from v3.1
            cursor.execute("PRAGMA table_info(media)")
            columns = cursor.fetchall()
            is_legacy = any(c[1] == 'image_hash' for c in columns)
            
            if is_legacy and version < 32:
                self._migrate_to_v32(conn)
            
            # V3.2 Schema Definition
            conn.execute('''
                CREATE TABLE IF NOT EXISTS media (
                    file_path TEXT PRIMARY KEY COLLATE NOCASE,
                    last_modified REAL,
                    metadata_json TEXT,
                    group_id TEXT COLLATE NOCASE,
                    location_id INTEGER,
                    thumbnail_path TEXT,
                    is_corrupted INTEGER DEFAULT 0,
                    is_in_trash INTEGER DEFAULT 0,
                    capture_date TEXT,
                    file_hash TEXT COLLATE NOCASE,
                    year INTEGER,
                    month INTEGER
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS duplicate_groups (
                    group_id TEXT PRIMARY KEY COLLATE NOCASE,
                    primary_file_path TEXT,
                    discovery_method TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS locations (
                    location_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    country TEXT, prefecture TEXT, city TEXT,
                    UNIQUE(country, prefecture, city)
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS media_features (
                    file_path TEXT PRIMARY KEY COLLATE NOCASE,
                    vector_blob BLOB,
                    salient_blob BLOB,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # Lightweight migration: Add salient_blob if missing
            cursor = conn.execute("PRAGMA table_info(media_features)")
            cols = [c[1] for c in cursor.fetchall()]
            if 'salient_blob' not in cols:
                try:
                    conn.execute("ALTER TABLE media_features ADD COLUMN salient_blob BLOB")
                    logger.info("Added salient_blob column to media_features table.")
                except Exception as e:
                    logger.warning(f"Failed to add salient_blob column: {e}")
            conn.execute('''
                CREATE TABLE IF NOT EXISTS faces (
                    face_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL COLLATE NOCASE,
                    vector_blob BLOB NOT NULL,
                    bbox_json TEXT,
                    cluster_id INTEGER,
                    is_ignored INTEGER DEFAULT 0,
                    frame_index INTEGER DEFAULT 0
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS clusters (
                    cluster_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    custom_name TEXT,
                    is_ignored INTEGER DEFAULT 0
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS ignored_person_vectors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vector_blob BLOB NOT NULL
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            
            # Indexes
            conn.execute('CREATE INDEX IF NOT EXISTS idx_media_group ON media (group_id COLLATE NOCASE)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_media_modified ON media (last_modified)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_media_capture_date ON media (capture_date DESC)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_faces_filepath ON faces (file_path COLLATE NOCASE)')
            
            conn.execute('PRAGMA user_version = 32')
            
            # Data Integrity Cleanup: Fix any groups that have NULL or empty discovery_method
            conn.execute("UPDATE duplicate_groups SET discovery_method = 'exact' WHERE discovery_method IS NULL OR discovery_method = ''")
            
            conn.commit()

    def _migrate_to_v32(self, conn):
        """Migration logic from v3.1 (monolithic) to v3.2 (normalized)."""
        logger.info("Migrating database to v3.2...")
        try:
            cursor = conn.execute("PRAGMA table_info(media)")
            cols = [c[1] for c in cursor.fetchall()]
            
            # Setup new tables
            conn.execute('''CREATE TABLE IF NOT EXISTS locations (
                location_id INTEGER PRIMARY KEY AUTOINCREMENT,
                country TEXT, prefecture TEXT, city TEXT, UNIQUE(country, prefecture, city))''')
            conn.execute('''CREATE TABLE IF NOT EXISTS duplicate_groups (
                group_id TEXT PRIMARY KEY COLLATE NOCASE, 
                primary_file_path TEXT, discovery_method TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            conn.execute('''CREATE TABLE IF NOT EXISTS media_features (
                file_path TEXT PRIMARY KEY COLLATE NOCASE, vector_blob BLOB, 
                salient_blob BLOB, last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

            # Data transfer: Locations
            if 'country' in cols and 'prefecture' in cols and 'city' in cols:
                conn.execute('''INSERT OR IGNORE INTO locations (country, prefecture, city)
                    SELECT DISTINCT country, prefecture, city FROM media
                    WHERE country IS NOT NULL OR prefecture IS NOT NULL OR city IS NOT NULL''')
            
            # Data transfer: Duplicate Groups
            if 'image_hash' in cols:
                dm_col = "discovery_method" if "discovery_method" in cols else "'exact'"
                conn.execute(f'''INSERT OR IGNORE INTO duplicate_groups (group_id, discovery_method)
                    SELECT DISTINCT image_hash, {dm_col} FROM media WHERE image_hash IS NOT NULL''')
            
            # Data transfer: Features
            if 'vector_blob' in cols:
                conn.execute('''INSERT OR IGNORE INTO media_features (file_path, vector_blob)
                    SELECT file_path, vector_blob FROM media WHERE vector_blob IS NOT NULL''')

            # Remap media table
            conn.execute('''CREATE TABLE media_new (
                file_path TEXT PRIMARY KEY COLLATE NOCASE,
                last_modified REAL, metadata_json TEXT, group_id TEXT COLLATE NOCASE,
                location_id INTEGER, thumbnail_path TEXT,
                is_corrupted INTEGER DEFAULT 0, is_in_trash INTEGER DEFAULT 0,
                capture_date TEXT, file_hash TEXT COLLATE NOCASE,
                year INTEGER, month INTEGER)''')
            
            # Determine which columns to read from old media table
            mapping_src = ['m.file_path', 'm.last_modified', 'm.metadata_json']
            mapping_src.append('m.image_hash' if 'image_hash' in cols else 'NULL')
            mapping_src.append('l.location_id')
            mapping_src.append('m.thumbnail_path' if 'thumbnail_path' in cols else 'NULL')
            mapping_src.append('m.is_corrupted' if 'is_corrupted' in cols else '0')
            mapping_src.append('m.is_in_trash' if 'is_in_trash' in cols else '0')
            mapping_src.append('m.capture_date' if 'capture_date' in cols else 'NULL')
            mapping_src.append('m.file_hash' if 'file_hash' in cols else 'NULL')
            mapping_src.append('m.year' if 'year' in cols else 'NULL')
            mapping_src.append('m.month' if 'month' in cols else 'NULL')

            mapping_sql = f'''
                INSERT INTO media_new (file_path, last_modified, metadata_json, group_id, location_id, 
                                      thumbnail_path, is_corrupted, is_in_trash, capture_date, file_hash,
                                      year, month)
                SELECT {", ".join(mapping_src)}
                FROM media m
                LEFT JOIN locations l ON 
                    COALESCE(m.country, '') = COALESCE(l.country, '') AND 
                    COALESCE(m.prefecture, '') = COALESCE(l.prefecture, '') AND 
                    COALESCE(m.city, '') = COALESCE(l.city, '')
            ''' if 'country' in cols else f'''
                INSERT INTO media_new (file_path, last_modified, metadata_json, group_id, location_id, 
                                      thumbnail_path, is_corrupted, is_in_trash, capture_date, file_hash,
                                      year, month)
                SELECT {", ".join(mapping_src)}
                FROM media m
            '''
            
            conn.execute(mapping_sql)
            conn.execute("DROP TABLE media")
            conn.execute("ALTER TABLE media_new RENAME TO media")
            logger.info("Database migration to v3.2 successful.")
        except Exception as e:
            logger.error(f"Database migration failed: {e}")
            raise

    def get_media(self, file_path):
        norm_path = os.path.normcase(os.path.abspath(file_path))
        with self.get_connection() as conn:
            cursor = conn.execute('''
                SELECT m.file_path, m.last_modified, m.metadata_json, m.group_id, 
                       0 AS lat, 0 AS lon, 0 AS alt, l.country, l.prefecture, l.city, 
                       SUBSTR(m.capture_date, 1, 4) AS yr, 
                       CAST(SUBSTR(m.capture_date, 6, 2) AS INTEGER) AS mo, 
                       m.thumbnail_path, m.is_corrupted, m.is_in_trash, 
                       m.capture_date, m.file_hash, f.vector_blob
                FROM media m
                LEFT JOIN locations l ON m.location_id = l.location_id
                LEFT JOIN media_features f ON m.file_path = f.file_path
                WHERE m.file_path = ?
            ''', (norm_path,))
            return cursor.fetchone()

    def add_media_batch(self, media_list):
        with self.get_connection() as conn:
            for m in media_list:
                norm_path = os.path.normcase(os.path.abspath(m[0]))
                if len(m) < 10: # Handle shortened tuples
                    conn.execute('''
                        INSERT OR REPLACE INTO media (file_path, last_modified, metadata_json, group_id, 
                                                      location_id, thumbnail_path, is_corrupted, is_in_trash, 
                                                      capture_date, file_hash)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (norm_path,) + m[1:])
                    continue

                # V3.2 18-column processing (histogram_blob and discovery_method purged from tuple)
                location_id = None
                country, pref, city = m[7], m[8], m[9]
                if any([country, pref, city]):
                    conn.execute('INSERT OR IGNORE INTO locations (country, prefecture, city) VALUES (?, ?, ?)', (country, pref, city))
                    cursor = conn.execute('SELECT location_id FROM locations WHERE COALESCE(country, "")=? AND COALESCE(prefecture, "")=? AND COALESCE(city, "")=?',
                                         (country or '', pref or '', city or ''))
                    location_id = cursor.fetchone()[0]

                conn.execute('''
                    INSERT OR REPLACE INTO media (file_path, last_modified, metadata_json, group_id, location_id,
                                                  thumbnail_path, is_corrupted, is_in_trash, capture_date, file_hash, year, month)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (norm_path, m[1], m[2], m[3], location_id, m[12], m[13], m[14], m[15], m[16], m[10], m[11]))
                
                if len(m) > 17 and m[17] is not None:
                    conn.execute('INSERT OR REPLACE INTO media_features (file_path, vector_blob) VALUES (?, ?)', (norm_path, m[17]))
            conn.commit()

    def update_media_vector_batch(self, vector_list):
        with self.get_connection() as conn:
            conn.executemany('''
                INSERT INTO media_features (file_path, vector_blob) VALUES (?, ?)
                ON CONFLICT(file_path) DO UPDATE SET vector_blob = excluded.vector_blob, last_updated = CURRENT_TIMESTAMP
            ''', [(os.path.normcase(os.path.abspath(p)), v) for v, p in vector_list])
            conn.commit()

    def update_salient_features_batch(self, salient_list):
        """salient_list: list of (file_path, salient_blob)"""
        if not salient_list: return
        with self.get_connection() as conn:
            conn.executemany('''
                INSERT INTO media_features (file_path, salient_blob) VALUES (?, ?)
                ON CONFLICT(file_path) DO UPDATE SET salient_blob = excluded.salient_blob, last_updated = CURRENT_TIMESTAMP
            ''', [(os.path.normcase(os.path.abspath(p)), v) for p, v in salient_list])
            conn.commit()

    def get_salient_feature(self, file_path):
        norm_path = os.path.normcase(os.path.abspath(file_path))
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT salient_blob FROM media_features WHERE file_path = ?', (norm_path,))
            row = cursor.fetchone()
            return row[0] if row else None

    def update_image_hashes_batch(self, hash_pairs):
        with self.get_connection() as conn:
            for item in hash_pairs:
                if len(item) == 3: h, m, p = item
                else: h, p, m = item[0], item[1], 'exact'
                norm_p = os.path.normcase(os.path.abspath(p))
                conn.execute('UPDATE media SET group_id = ? WHERE file_path = ?', (h, norm_p))
                if h: 
                    # Use REPLACE to ensure discovery_method is correctly set/updated
                    conn.execute('INSERT OR REPLACE INTO duplicate_groups (group_id, discovery_method) VALUES (?, ?)', (h, m))
            conn.commit()


    def add_faces_batch(self, faces_list):
        norm_faces = []
        for f in faces_list:
            f_list = list(f)
            f_list[0] = os.path.normcase(os.path.abspath(f_list[0]))
            norm_faces.append(tuple(f_list))
            
        with self.get_connection() as conn:
            conn.executemany('''
                INSERT INTO faces (file_path, vector_blob, bbox_json)
                VALUES (?, ?, ?)
            ''', norm_faces)
            conn.commit()

    def update_face_clusters_batch(self, face_ids, labels):
        with self.get_connection() as conn:
            data = [(int(label), int(fid)) for fid, label in zip(face_ids, labels)]
            conn.executemany('UPDATE faces SET cluster_id = ? WHERE face_id = ?', data)
            conn.commit()

    def get_all_faces(self):
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT face_id, file_path, vector_blob, cluster_id FROM faces')
            return cursor.fetchall()

    def get_duplicates(self):
        with self.get_connection() as conn:
            # JOIN duplicate_groups to verify existence
            cursor = conn.execute('''
                SELECT m.group_id FROM media m
                JOIN duplicate_groups g ON m.group_id = g.group_id
                WHERE m.group_id IS NOT NULL 
                GROUP BY m.group_id 
                HAVING COUNT(*) > 1
            ''')
            hashes = [row[0] for row in cursor.fetchall()]
            all_groups = []
            for h in hashes:
                cursor = conn.execute('SELECT file_path, metadata_json, is_in_trash FROM media WHERE group_id = ?', (h,))
                group = []
                for row in cursor.fetchall():
                    group.append({
                        "file_path": row[0],
                        "metadata": json.loads(row[1]) if row[1] else {},
                        "group_id": h,
                        "is_in_trash": row[2]
                    })
                all_groups.append(group)
            return all_groups

    def get_clusters(self, include_ignored=False):
        with self.get_connection() as conn:
            # Only return clusters that have at least one non-corrupted media file
            cursor = conn.execute('''
                SELECT DISTINCT f.cluster_id
                FROM faces f
                JOIN media m ON f.file_path = m.file_path
                WHERE m.is_corrupted = 0 AND f.cluster_id IS NOT NULL AND f.cluster_id != -1
            ''')
            cids = [row[0] for row in cursor.fetchall()]

            results = []
            for cid in cids:
                query = 'SELECT custom_name, is_ignored FROM clusters WHERE cluster_id = ?'
                cursor = conn.execute(query, (cid,))
                row = cursor.fetchone()

                name = row[0] if row else None
                ignored = row[1] if row else 0

                if not include_ignored and ignored:
                    continue

                results.append((cid, name))

            # Sort by name (if exists) then by id
            results.sort(key=lambda x: (str(x[1]) if x[1] else "", x[0] if x[0] is not None else -1))
            return results
    def get_faces_for_cluster(self, cluster_id):
        with self.get_connection() as conn:
            query = 'SELECT face_id, file_path, bbox_json FROM faces WHERE cluster_id = ?'
            cursor = conn.execute(query, (cluster_id,))
            return [{"face_id": r[0], "file_path": r[1], "bbox": json.loads(r[2]) if r[2] else None} for r in cursor.fetchall()]

    def get_faces_with_meta_for_cluster(self, cluster_id):
        with self.get_connection() as conn:
            query = 'SELECT f.face_id, f.file_path, f.bbox_json, m.metadata_json, f.frame_index FROM faces f JOIN media m ON f.file_path = m.file_path WHERE f.cluster_id = ?'
            cursor = conn.execute(query, (cluster_id,))
            return [{"face_id": r[0], "file_path": r[1], "bbox": json.loads(r[2]) if r[2] else None, "meta": json.loads(r[3]) if r[3] else {}, "frame_index": r[4]} for r in cursor.fetchall()]

    def get_faces_with_meta_unclassified(self):
        with self.get_connection() as conn:
            query = 'SELECT f.face_id, f.file_path, f.bbox_json, m.metadata_json, f.frame_index FROM faces f JOIN media m ON f.file_path = m.file_path WHERE f.cluster_id IS NULL OR f.cluster_id = -1'
            cursor = conn.execute(query)
            return [{"face_id": r[0], "file_path": r[1], "bbox": json.loads(r[2]) if r[2] else None, "meta": json.loads(r[3]) if r[3] else {}, "frame_index": r[4]} for r in cursor.fetchall()]

    def move_face_to_cluster(self, face_id, target_cluster_id):
        with self.get_connection() as conn:
            conn.execute('UPDATE faces SET cluster_id = ? WHERE face_id = ?', (target_cluster_id, face_id))
            conn.commit()

    def add_face_manual(self, file_path, cluster_id):
        norm_path = os.path.normcase(os.path.abspath(file_path))
        with self.get_connection() as conn:
            conn.execute('INSERT INTO faces (file_path, cluster_id, bbox_json) VALUES (?, ?, ?)', (norm_path, cluster_id, '[]'))
            conn.commit()

    def upsert_cluster(self, cluster_id, name, is_ignored=None):
        with self.get_connection() as conn:
            if name and name.strip():
                cursor = conn.execute('SELECT cluster_id FROM clusters WHERE custom_name = ? AND cluster_id != ?', (name.strip(), cluster_id))
                target = cursor.fetchone()
                if target:
                    target_id = target[0]
                    conn.execute('UPDATE faces SET cluster_id = ? WHERE cluster_id = ?', (target_id, cluster_id))
                    conn.execute('DELETE FROM clusters WHERE cluster_id = ?', (cluster_id,))
                    conn.commit()
                    return True
            if is_ignored is not None:
                conn.execute('INSERT INTO clusters (cluster_id, custom_name, is_ignored) VALUES (?, ?, ?) ON CONFLICT(cluster_id) DO UPDATE SET custom_name=excluded.custom_name, is_ignored=excluded.is_ignored', (cluster_id, name, is_ignored))
            else:
                conn.execute('INSERT INTO clusters (cluster_id, custom_name) VALUES (?, ?) ON CONFLICT(cluster_id) DO UPDATE SET custom_name=excluded.custom_name', (cluster_id, name))
            conn.commit()
            return False

    def create_cluster_manual(self, name):
        with self.get_connection() as conn:
            if name and name.strip():
                cursor = conn.execute('SELECT cluster_id FROM clusters WHERE custom_name = ?', (name.strip(),))
                row = cursor.fetchone()
                if row: return row[0]
            cursor = conn.execute('INSERT INTO clusters (custom_name) VALUES (?)', (name,))
            new_id = cursor.lastrowid
            conn.commit()
            return new_id

    def upsert_clusters_batch(self, cluster_data_list):
        with self.get_connection() as conn:
            conn.executemany('INSERT INTO clusters (cluster_id, custom_name, is_ignored) VALUES (?, ?, ?) ON CONFLICT(cluster_id) DO UPDATE SET custom_name=excluded.custom_name, is_ignored=excluded.is_ignored', cluster_data_list)
            conn.commit()

    def get_cluster_representative_data(self, cluster_id):
        with self.get_connection() as conn:
            query = 'SELECT f.file_path, f.bbox_json, m.metadata_json FROM faces f JOIN media m ON f.file_path = m.file_path WHERE f.cluster_id = ? LIMIT 1'
            cursor = conn.execute(query, (cluster_id,))
            row = cursor.fetchone()
            if row: return row[0], json.loads(row[1]) if row[1] else None, json.loads(row[2]) if row[2] else {}
            return None, None, {}

    def delete_media(self, file_path):
        norm_path = os.path.normcase(os.path.abspath(file_path))
        with self.get_connection() as conn:
            conn.execute('DELETE FROM faces WHERE file_path = ?', (norm_path,))
            conn.execute('DELETE FROM media WHERE file_path = ?', (norm_path,))
            conn.execute('DELETE FROM media_features WHERE file_path = ?', (norm_path,))
            conn.commit()

    def delete_media_batch(self, file_paths):
        if not file_paths: return
        norm_paths = [os.path.normcase(os.path.abspath(p)) for p in file_paths]
        batch_size = 500
        with self.get_connection() as conn:
            for i in range(0, len(norm_paths), batch_size):
                chunk = norm_paths[i:i + batch_size]
                placeholders = ','.join(['?'] * len(chunk))
                conn.execute(f'DELETE FROM faces WHERE file_path IN ({placeholders})', chunk)
                conn.execute(f'DELETE FROM media WHERE file_path IN ({placeholders})', chunk)
                conn.execute(f'DELETE FROM media_features WHERE file_path IN ({placeholders})', chunk)
            conn.commit()

    def get_media_paths_in_folder(self, folder_path):
        norm_path = os.path.abspath(os.path.normpath(folder_path))
        if not norm_path.endswith(os.path.sep): norm_path += os.path.sep
        pattern = norm_path.replace('[', '[[]').replace('%', '[%]') + '%'
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT file_path FROM media WHERE file_path LIKE ? COLLATE NOCASE', (pattern,))
            return [row[0] for row in cursor.fetchall()]

    def get_all_media_paths(self):
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT file_path FROM media')
            return [row[0] for row in cursor.fetchall()]

    def merge_duplicate_paths_batch(self, merges):
        if not merges: return
        with self.get_connection() as conn:
            for canonical_path, redundant_paths in merges:
                if not redundant_paths: continue
                for old_path in redundant_paths:
                    conn.execute("UPDATE faces SET file_path = ? WHERE file_path = ?", (canonical_path, old_path))
                    conn.execute("UPDATE media_features SET file_path = ? WHERE file_path = ?", (canonical_path, old_path))
                placeholders = ','.join(['?'] * len(redundant_paths))
                conn.execute(f"DELETE FROM media WHERE file_path IN ({placeholders})", redundant_paths)
            conn.commit()

    def get_duplicate_groups(self, root_folder=None):
        trash_filter = "m.is_in_trash = 0 AND" # We usually don't want to show trash as primary group members in listing
        
        # Folder filter logic: We only want groups that contain at least one file from the root_folder tree
        folder_clause = ""
        params = []
        if root_folder:
            norm_root = os.path.abspath(os.path.normpath(root_folder)) + os.path.sep
            pattern = norm_root.replace('[', '[[]').replace('%', '[%]') + '%'
            folder_clause = "AND group_id IN (SELECT group_id FROM media WHERE file_path LIKE ? COLLATE NOCASE)"
            params.append(pattern)

        with self.get_connection() as conn:
            # Optimized single query to fetch all files that have a duplicate hash
            query = f'''
                SELECT m.file_path, m.metadata_json, m.group_id, m.is_in_trash, dg.discovery_method
                FROM media m
                JOIN duplicate_groups dg ON m.group_id = dg.group_id
                WHERE m.group_id IN (
                    SELECT group_id FROM media 
                    WHERE group_id IS NOT NULL AND group_id != ''
                    GROUP BY group_id HAVING COUNT(*) > 1
                ) {folder_clause}
                ORDER BY m.group_id, m.is_in_trash ASC
            '''
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            
            all_groups = []
            current_hash = None
            current_group = []
            
            for row in rows:
                path, meta, h, it, dm = row
                item = {
                    "file_path": path,
                    "metadata": json.loads(meta) if meta else {},
                    "group_id": h,
                    "is_in_trash": it,
                    "discovery_method": dm
                }
                
                if h != current_hash:
                    if current_group:
                        all_groups.append(current_group)
                    current_group = [item]
                    current_hash = h
                else:
                    current_group.append(item)
            
            if current_group:
                all_groups.append(current_group)
                
            return all_groups

    def clear_all_data(self):
        with self.get_connection() as conn:
            conn.execute('DELETE FROM faces')
            conn.execute('DELETE FROM media')
            conn.execute('DELETE FROM clusters')
            conn.commit()

    def clear_faces_for_file(self, file_path):
        norm_path = os.path.normcase(os.path.abspath(file_path))
        with self.get_connection() as conn:
            conn.execute('DELETE FROM faces WHERE file_path = ?', (norm_path,))
            conn.commit()

    def clear_face_data(self, folder_path=None):
        """
        Clears face data from the 'faces' table. 
        If folder_path is provided, only faces belonging to that folder tree are deleted.
        If folder_path is None, both 'faces' and 'clusters' tables are emptied.
        """
        with self.get_connection() as conn:
            if folder_path:
                # Use project standard normalization
                norm_root = os.path.normcase(os.path.abspath(folder_path))
                if not norm_root.endswith(os.path.sep):
                    norm_root += os.path.sep
                
                # ESCAPE '[' and '%' for LIKE clause
                pattern = norm_root.replace('[', '[[]').replace('%', '[%]') + '%'
                
                conn.execute('DELETE FROM faces WHERE file_path LIKE ? COLLATE NOCASE', (pattern,))
                logger.info(f"Cleared face data for folder tree: {folder_path}")
            else:
                conn.execute('DELETE FROM faces')
                conn.execute('DELETE FROM clusters')
                conn.execute('DELETE FROM ignored_person_vectors')
                # Reset auto-increment sequences for a clean "hard" reset
                conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('faces', 'clusters', 'ignored_person_vectors')")
                logger.info("Cleared all face, cluster, and ignored vector data. IDs reset to 1.")
            
            conn.commit()
            # Ensure consistency and sync WAL to main DB
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

    def get_duplicate_stats(self, include_trash=False, root_folder=None, discovery_filter=None):
        trash_cond = "is_in_trash = 0" if not include_trash else "1=1"
        params = []
        
        # 1. Identify groups that are duplicates globally (count > 1 across the whole DB)
        subquery = f"SELECT group_id FROM media WHERE {trash_cond} GROUP BY group_id HAVING COUNT(*) > 1"
        
        # 2. Select these groups from duplicate_groups, and filter by folder presence if root_folder is given
        query = f"SELECT dg.group_id, dg.discovery_method FROM duplicate_groups dg WHERE dg.group_id IN ({subquery})"
        
        if root_folder:
            norm_root = os.path.abspath(os.path.normpath(root_folder)) + os.path.sep
            pattern = norm_root.replace('[', '[[]').replace('%', '[%]') + '%'
            query += f" AND EXISTS (SELECT 1 FROM media m_ex WHERE m_ex.group_id = dg.group_id AND m_ex.file_path LIKE ? COLLATE NOCASE AND m_ex.{trash_cond})"
            params.append(pattern)
            
        # 3. Apply discovery method filter
        if discovery_filter:
            if discovery_filter == 'ai_local':
                query = f"SELECT group_id, discovery_method FROM ({query}) WHERE discovery_method LIKE 'ai_%'"
            else:
                query = f"SELECT group_id, discovery_method FROM ({query}) WHERE discovery_method = ?"
                params.append(discovery_filter)
        
        with self.get_connection() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            if not rows: return 0, 0, {}
            
            group_count = len(rows)
            counts = {}
            ids = []
            for row in rows:
                m = row[1] or 'exact'
                counts[m] = counts.get(m, 0) + 1
                ids.append(row[0])
                
            # 4. Count total duplicate files within the target context
            # We count files that belong to the identified duplicate groups AND match the folder/trash filter
            f_params = list(ids)
            f_query = f"SELECT COUNT(*) FROM media WHERE group_id IN ({','.join(['?']*len(ids))}) AND {trash_cond}"
            if root_folder:
                f_query += " AND file_path LIKE ? COLLATE NOCASE"
                f_params.append(pattern)
                
            cursor = conn.execute(f_query, f_params)
            total_files = cursor.fetchone()[0]
            return group_count, total_files, counts

    def clear_ai_duplicate_groups(self, root_folder=None):
        """
        Resets AI-based duplicate associations (group_id = NULL) for the given scope.
        Keeps 'exact' (MD5) groups intact.
        """
        params = []
        where_clause = "WHERE group_id IN (SELECT group_id FROM duplicate_groups WHERE discovery_method LIKE 'ai_%')"
        
        if root_folder:
            norm_root = os.path.abspath(os.path.normpath(root_folder)) + os.path.sep
            pattern = norm_root.replace('[', '[[]').replace('%', '[%]') + '%'
            where_clause += " AND file_path LIKE ? COLLATE NOCASE"
            params.append(pattern)
        
        with self.get_connection() as conn:
            conn.execute(f"UPDATE media SET group_id = NULL {where_clause}", params)
            conn.commit()
            logger.info(f"Cleared AI duplicate groups for scope: {root_folder or 'Global'}")

    def release_files_from_groups(self, file_paths):
        """Removes duplicate group association from specified files and cleans up orphaned groups."""
        if not file_paths:
            # Still perform cleanup of orphaned groups even if no specific files are released
            with self.get_connection() as conn:
                self._cleanup_orphaned_groups(conn)
                conn.commit()
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            return
            
        norm_paths = [os.path.normcase(os.path.abspath(p)) for p in file_paths]
        
        with self.get_connection() as conn:
            # 1. Release specified files from their groups
            batch_size = 500
            for i in range(0, len(norm_paths), batch_size):
                chunk = norm_paths[i:i + batch_size]
                placeholders = ','.join(['?'] * len(chunk))
                conn.execute(f"UPDATE media SET group_id = NULL WHERE file_path IN ({placeholders})", chunk)
            
            # 2. Cleanup
            self._cleanup_orphaned_groups(conn)
            
            conn.commit()
            # Ensure changes are synced from WAL to main DB file for UI consistency
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            logger.info(f"Released {len(norm_paths)} files from duplicate groups and cleaned up orphaned groups.")

    def _cleanup_orphaned_groups(self, conn):
        """Internal helper to clean up duplicate_groups and orphaned group_ids in media table."""
        # 1. Cleanup duplicate_groups that no longer have at least 2 associated files
        conn.execute("""
            DELETE FROM duplicate_groups 
            WHERE group_id NOT IN (
                SELECT group_id FROM media 
                WHERE group_id IS NOT NULL 
                GROUP BY group_id 
                HAVING COUNT(*) > 1
            )
        """)
        
        # 2. Clear group_id for orphaned single members in media table
        conn.execute("""
            UPDATE media SET group_id = NULL 
            WHERE group_id IS NOT NULL AND group_id NOT IN (SELECT group_id FROM duplicate_groups)
        """)

    def release_duplicate_group(self, group_id):
        """Removes all members from a specific group and purges the group record."""
        # Find all files belonging to this group
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT file_path FROM media WHERE group_id = ?", (group_id,))
            file_paths = [row[0] for row in cursor.fetchall()]
        
        # Use release_files_from_groups to handle the updates and cleanup consistently
        self.release_files_from_groups(file_paths)

    def _get_filter_clause(self, cluster_id, year, month, location, include_trash, root_folder):
        clauses = []
        params = []
        if not include_trash: clauses.append("m.is_in_trash = 0")
        if root_folder:
            norm_root = os.path.abspath(os.path.normpath(root_folder)) + os.path.sep
            pattern = norm_root.replace('[', '[[]').replace('%', '[%]') + '%'
            clauses.append("m.file_path LIKE ? COLLATE NOCASE")
            params.append(pattern)
        if cluster_id is not None:
            if cluster_id == -1: clauses.append("m.file_path NOT IN (SELECT file_path FROM faces)")
            elif cluster_id == -2: clauses.append("m.group_id IN (SELECT group_id FROM media GROUP BY group_id HAVING COUNT(*) > 1)")
            elif cluster_id == -3: clauses.append("m.is_corrupted = 1")
            else:
                clauses.append("EXISTS (SELECT 1 FROM faces f WHERE f.file_path = m.file_path AND f.cluster_id = ?)")
                params.append(cluster_id)
        if year:
            clauses.append("SUBSTR(m.capture_date, 1, 4) = ?")
            params.append(str(year))
        if month:
            clauses.append("CAST(SUBSTR(m.capture_date, 6, 2) AS INTEGER) = ?")
            params.append(int(month))
        if location:
            clauses.append("(l.city = ? OR l.prefecture = ? OR l.country = ?)")
            params.extend([location, location, location])
        return clauses, params

    def get_years(self, cluster_id=None, include_trash=False, root_folder=None):
        clauses, params = self._get_filter_clause(cluster_id, None, None, None, include_trash, root_folder)
        year_expr = "SUBSTR(m.capture_date, 1, 4)"
        where = " WHERE " + " AND ".join(clauses + [f"{year_expr} IS NOT NULL", f"{year_expr} != ''"]) if clauses else f" WHERE {year_expr} IS NOT NULL AND {year_expr} != ''"
        query = f"SELECT DISTINCT {year_expr} FROM media m LEFT JOIN locations l ON m.location_id = l.location_id {where}"
        with self.get_connection() as conn:
            cursor = conn.execute(query, params)
            return sorted([r[0] for r in cursor.fetchall() if r[0]], reverse=True)

    def get_months(self, cluster_id, year, include_trash=False, root_folder=None):
        clauses, params = self._get_filter_clause(cluster_id, year, None, None, include_trash, root_folder)
        month_expr = "CAST(SUBSTR(m.capture_date, 6, 2) AS INTEGER)"
        where = " WHERE " + " AND ".join(clauses + [f"{month_expr} IS NOT NULL", f"{month_expr} != 0"]) if clauses else f" WHERE {month_expr} IS NOT NULL AND {month_expr} != 0"
        query = f"SELECT DISTINCT {month_expr} FROM media m LEFT JOIN locations l ON m.location_id = l.location_id {where}"
        with self.get_connection() as conn:
            cursor = conn.execute(query, params)
            return sorted([r[0] for r in cursor.fetchall() if r[0]], reverse=True)

    def get_locations(self, cluster_id, year, month, include_trash=False, root_folder=None):
        clauses, params = self._get_filter_clause(cluster_id, year, month, None, include_trash, root_folder)
        loc_expr = "CASE WHEN l.country IN ('Japan', '日本', 'JP') THEN l.prefecture ELSE l.country END"
        where = " WHERE " + " AND ".join(clauses + [f"{loc_expr} IS NOT NULL", f"{loc_expr} != ''"])
        # Corrected JOIN: m.location_id = l.location_id
        query = f"SELECT DISTINCT {loc_expr} FROM media m JOIN locations l ON m.location_id = l.location_id {where}"
        with self.get_connection() as conn:
            cursor = conn.execute(query, params)
            return sorted([r[0] for r in cursor.fetchall() if r[0]])

    def get_media_paged(self, cluster_id, year, month, location=None, limit=50, offset=0, include_trash=False, root_folder=None, discovery_filter=None):
        clauses, params = self._get_filter_clause(cluster_id, year, month, location, include_trash, root_folder)
        if cluster_id == -2 and discovery_filter:
            if discovery_filter == 'ai_local':
                clauses.append("dg.discovery_method LIKE 'ai_%'")
            else:
                # If discovery_filter is set and not 'ai_local', it's 'exact'
                clauses.append("dg.discovery_method = ?")
                params.append(discovery_filter)

        # Duplicate badge logic
        scope_where = " AND ".join(["m2.is_in_trash = 0"] if not include_trash else [])
        if scope_where: scope_where = " AND " + scope_where
        is_dupe_sql = f"EXISTS (SELECT 1 FROM media m2 WHERE m2.group_id = m.group_id AND m2.file_path != m.file_path{scope_where}) as is_duplicate"
        
        # Normalized fields (Fetch city/prefecture from l)
        query = f"""
            SELECT m.file_path, m.metadata_json, m.group_id, m.is_in_trash, {is_dupe_sql},
            (SELECT GROUP_CONCAT(DISTINCT COALESCE(f.cluster_id, -1) || ':' || COALESCE(c.custom_name, ''))
             FROM faces f LEFT JOIN clusters c ON f.cluster_id = c.cluster_id
             WHERE f.file_path = m.file_path AND (c.is_ignored IS NULL OR c.is_ignored = 0)) as person_tags,
            m.thumbnail_path, dg.discovery_method, l.city, l.prefecture, l.country, m.capture_date
            FROM media m
            LEFT JOIN locations l ON m.location_id = l.location_id
            LEFT JOIN duplicate_groups dg ON m.group_id = dg.group_id
        """
        if clauses: query += " WHERE " + " AND ".join(clauses)
        
        # Sorting
        if cluster_id == -2: query += " ORDER BY m.group_id, m.is_in_trash ASC, m.capture_date DESC"
        else: query += " ORDER BY m.capture_date DESC"
        
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        with self.get_connection() as conn:
            cursor = conn.execute(query, params)
            return [{
                "file_path": r[0], "metadata": json.loads(r[1]) if r[1] else {}, "group_id": r[2],
                "is_in_trash": r[3], "is_duplicate": bool(r[4]), "person_tags": r[5],
                "thumbnail_path": r[6], "discovery_method": r[7],
                "city": r[8], "prefecture": r[9], "country": r[10], "capture_date": r[11]
            } for r in cursor.fetchall()]

    def update_face_cluster(self, face_id, cluster_id):
        with self.get_connection() as conn:
            conn.execute("UPDATE faces SET cluster_id = ? WHERE face_id = ?", (cluster_id, face_id))
            conn.commit()

    def remove_face(self, face_id):
        with self.get_connection() as conn:
            conn.execute("DELETE FROM faces WHERE face_id = ?", (face_id,))
            conn.commit()

    def remove_face_batch(self, face_ids):
        if not face_ids: return
        with self.get_connection() as conn:
            placeholders = ','.join(['?'] * len(face_ids))
            conn.execute(f"DELETE FROM faces WHERE face_id IN ({placeholders})", face_ids)
            conn.commit()

    def get_faces_for_file(self, file_path):
        norm_path = os.path.normcase(os.path.abspath(file_path))
        with self.get_connection() as conn:
            query = "SELECT f.face_id, f.cluster_id, c.custom_name, f.bbox_json FROM faces f LEFT JOIN clusters c ON f.cluster_id = c.cluster_id WHERE f.file_path = ?"
            cursor = conn.execute(query, (norm_path,))
            return cursor.fetchall()

    def get_all_clusters(self):
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT cluster_id, custom_name FROM clusters WHERE is_ignored = 0")
            return cursor.fetchall()

    def reset_all_people(self):
        """
        Deletes all clusters, ignored person vectors, and resets face cluster_ids.
        """
        with self.get_connection() as conn:
            conn.execute('DELETE FROM clusters')
            conn.execute('DELETE FROM ignored_person_vectors')
            conn.execute('UPDATE faces SET cluster_id = NULL')
            conn.commit()

    def ignore_cluster(self, cluster_id):
        """
        Saves a representative vector to ignored_person_vectors, 
        then deletes all face records and the cluster itself.
        """
        with self.get_connection() as conn:
            # 1. Get a representative vector (the first one)
            cursor = conn.execute("SELECT vector_blob FROM faces WHERE cluster_id = ? LIMIT 1", (cluster_id,))
            row = cursor.fetchone()
            if row:
                vector_blob = row[0]
                # 2. Save to ignored_person_vectors
                conn.execute("INSERT INTO ignored_person_vectors (vector_blob) VALUES (?)", (vector_blob,))
            
            # 3. Delete traces
            conn.execute("DELETE FROM faces WHERE cluster_id = ?", (cluster_id,))
            conn.execute("DELETE FROM clusters WHERE cluster_id = ?", (cluster_id,))
            conn.commit()
            logger.info(f"Ignored cluster {cluster_id} and purged associated face records.")

    def get_ignored_vectors(self):
        """Returns all ignored vectors as a list of numpy arrays (float32)."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT vector_blob FROM ignored_person_vectors")
            return [np.frombuffer(row[0], dtype=np.float32) for row in cursor.fetchall()]

    def get_suggested_face_merges(self, suggestion_thresh=0.65, auto_merge_thresh=0.85):
        """
        Finds pairs of clusters that are similar but not merged.
        Calculation is based on the centroid (mean) of all embeddings in each cluster.
        """
        with self.get_connection() as conn:
            # 1. Fetch active clusters
            cursor = conn.execute("SELECT cluster_id FROM clusters WHERE is_ignored = 0")
            cids = [row[0] for row in cursor.fetchall() if row[0] is not None]
            
            if len(cids) < 2: return []
            
            # 2. Fetch all embeddings grouping by cluster
            placeholders = ','.join(['?'] * len(cids))
            query = f"SELECT cluster_id, vector_blob FROM faces WHERE cluster_id IN ({placeholders})"
            cursor = conn.execute(query, cids)
            cluster_faces = {}
            for cid, blob in cursor.fetchall():
                if cid not in cluster_faces: cluster_faces[cid] = []
                cluster_faces[cid].append(np.frombuffer(blob, dtype=np.float32))
            
            # 3. Compute Centroids (normalized means)
            centroids = {}
            for cid, embs in cluster_faces.items():
                if not embs: continue
                avg = np.mean(embs, axis=0)
                norm = np.linalg.norm(avg)
                if norm > 0:
                    centroids[cid] = avg / (norm + 1e-6)
            
            # 4. Pairwise similarity comparison
            suggestions = []
            cid_list = list(centroids.keys())
            for i in range(len(cid_list)):
                for j in range(i + 1, len(cid_list)):
                    c1, c2 = cid_list[i], cid_list[j]
                    # Cosine similarity = Dot product for normalized vectors
                    sim = float(np.dot(centroids[c1], centroids[c2]))
                    
                    if suggestion_thresh <= sim < auto_merge_thresh:
                        suggestions.append((c1, c2, sim))
            
            # Sort by similarity descending
            suggestions.sort(key=lambda x: x[2], reverse=True)
            return suggestions

    def merge_clusters(self, source_cluster_id, target_cluster_id, target_name=None):
        """Consolidates two clusters into target_cluster_id."""
        with self.get_connection() as conn:
            # Update faces
            conn.execute("UPDATE faces SET cluster_id = ? WHERE cluster_id = ?", (target_cluster_id, source_cluster_id))
            # Delete old cluster record
            conn.execute("DELETE FROM clusters WHERE cluster_id = ?", (source_cluster_id,))
            if target_name:
                conn.execute("UPDATE clusters SET custom_name = ? WHERE cluster_id = ?", (target_name, target_cluster_id))
            conn.commit()
            logger.info(f"Merged cluster {source_cluster_id} into {target_cluster_id}")

    def get_setting(self, key, default=None):
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT value FROM settings WHERE key = ?', (key,))
            row = cursor.fetchone()
            return row[0] if row else default

    def save_setting(self, key, value):
        with self.get_connection() as conn:
            conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, str(value)))
            conn.commit()

