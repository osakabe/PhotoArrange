import sqlite3
import json
import os
from .utils import get_app_data_dir

class Database:
    def __init__(self, db_path=None):
        if db_path is None:
            self.db_path = os.path.join(get_app_data_dir(), "media_cache.db")
        else:
            self.db_path = db_path
        self.init_db()

    def get_connection(self):
        return sqlite3.connect(self.db_path)

    def init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS media (
                    file_path TEXT PRIMARY KEY,
                    last_modified REAL,
                    metadata_json TEXT,
                    image_hash TEXT,
                    latitude REAL,
                    longitude REAL,
                    altitude REAL,
                    country TEXT,
                    prefecture TEXT,
                    city TEXT,
                    year INTEGER,
                    month INTEGER
                )
            ''')
            
            cursor.execute("PRAGMA table_info(media)")
            existing_cols = [row[1] for row in cursor.fetchall()]
            extra_cols = [
                ("latitude", "REAL"), ("longitude", "REAL"), ("altitude", "REAL"),
                ("country", "TEXT"), ("prefecture", "TEXT"), ("city", "TEXT"),
                ("year", "INTEGER"), ("month", "INTEGER")
            ]
            for col_name, col_type in extra_cols:
                if col_name not in existing_cols:
                    cursor.execute(f"ALTER TABLE media ADD COLUMN {col_name} {col_type}")
                    
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS faces (
                    face_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT,
                    vector_blob BLOB,
                    cluster_id INTEGER,
                    bbox_json TEXT,
                    FOREIGN KEY (file_path) REFERENCES media (file_path)
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS clusters (
                    cluster_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    custom_name TEXT,
                    is_ignored INTEGER DEFAULT 0
                )
            ''')
            
            # Migration check
            cursor.execute("PRAGMA table_info(clusters)")
            cols = [row[1] for row in cursor.fetchall()]
            if "is_ignored" not in cols:
                cursor.execute("ALTER TABLE clusters ADD COLUMN is_ignored INTEGER DEFAULT 0")
            
            # Indexing for performance
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_hash ON media (image_hash)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_modified ON media (last_modified)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_faces_filepath ON faces (file_path)')
            
            conn.commit()

    def get_media(self, file_path):
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT * FROM media WHERE file_path = ?', (file_path,))
            return cursor.fetchone()

    def add_media_batch(self, media_list):
        with self.get_connection() as conn:
            conn.executemany('''
                INSERT OR REPLACE INTO media (
                    file_path, last_modified, metadata_json, image_hash, 
                    latitude, longitude, altitude, country, prefecture, city,
                    year, month
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', media_list)
            conn.commit()

    def add_faces_batch(self, faces_list):
        with self.get_connection() as conn:
            conn.executemany('''
                INSERT INTO faces (file_path, vector_blob, bbox_json)
                VALUES (?, ?, ?)
            ''', faces_list)
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
            cursor = conn.execute('''
                SELECT image_hash FROM media 
                WHERE image_hash IS NOT NULL 
                GROUP BY image_hash HAVING COUNT(*) > 1
            ''')
            hashes = [row[0] for row in cursor.fetchall()]
            all_groups = []
            for h in hashes:
                cursor = conn.execute('SELECT file_path, metadata_json FROM media WHERE image_hash = ?', (h,))
                group = []
                for row in cursor.fetchall():
                    group.append({
                        "file_path": row[0],
                        "metadata": json.loads(row[1]) if row[1] else {},
                        "group_hash": h
                    })
                all_groups.append(group)
            return all_groups

    def get_clusters(self, include_ignored=False):
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT DISTINCT cluster_id FROM faces')
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
            return results

    def upsert_cluster(self, cluster_id, name, is_ignored=None):
        with self.get_connection() as conn:
            if is_ignored is not None:
                conn.execute('''
                    INSERT INTO clusters (cluster_id, custom_name, is_ignored) VALUES (?, ?, ?)
                    ON CONFLICT(cluster_id) DO UPDATE SET custom_name=excluded.custom_name, is_ignored=excluded.is_ignored
                ''', (cluster_id, name, is_ignored))
            else:
                conn.execute('''
                    INSERT INTO clusters (cluster_id, custom_name) VALUES (?, ?)
                    ON CONFLICT(cluster_id) DO UPDATE SET custom_name=excluded.custom_name
                ''', (cluster_id, name))

    def get_cluster_representative_data(self, cluster_id):
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT file_path, bbox_json FROM faces WHERE cluster_id = ? LIMIT 1', (cluster_id,))
            row = cursor.fetchone()
            if row:
                return row[0], json.loads(row[1]) if row[1] else None
            return None, None

    def delete_media(self, file_path):
        with self.get_connection() as conn:
            conn.execute('DELETE FROM faces WHERE file_path = ?', (file_path,))
            conn.execute('DELETE FROM media WHERE file_path = ?', (file_path,))
            conn.commit()

    def get_duplicate_groups(self):
        with self.get_connection() as conn:
            # Optimized single query to fetch all files that have a duplicate hash
            query = '''
                SELECT file_path, metadata_json, image_hash 
                FROM media 
                WHERE image_hash IN (
                    SELECT image_hash FROM media 
                    WHERE image_hash IS NOT NULL 
                    GROUP BY image_hash HAVING COUNT(*) > 1
                )
                ORDER BY image_hash
            '''
            cursor = conn.execute(query)
            rows = cursor.fetchall()
            
            all_groups = []
            current_hash = None
            current_group = []
            
            for row in rows:
                path, meta, h = row
                item = {
                    "file_path": path,
                    "metadata": json.loads(meta) if meta else {},
                    "group_hash": h
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

    def get_years(self, cluster_id=None):
        query = ""
        params = []
        if cluster_id is None:
            query = "SELECT DISTINCT year FROM media WHERE year IS NOT NULL"
        elif cluster_id == -1:
            query = "SELECT DISTINCT year FROM media WHERE year IS NOT NULL AND file_path NOT IN (SELECT file_path FROM faces)"
        elif cluster_id == -2:
            query = "SELECT DISTINCT year FROM media WHERE year IS NOT NULL AND image_hash IN (SELECT image_hash FROM media GROUP BY image_hash HAVING COUNT(*) > 1)"
        else:
            query = "SELECT DISTINCT year FROM media m JOIN faces f ON m.file_path = f.file_path WHERE f.cluster_id = ? AND m.year IS NOT NULL"
            params = [cluster_id]
        with self.get_connection() as conn:
            cursor = conn.execute(query, params)
            return sorted([row[0] for row in cursor.fetchall()], reverse=True)

    def get_months(self, cluster_id, year):
        query = ""
        params = [year]
        if cluster_id is None:
            query = "SELECT DISTINCT month FROM media WHERE year = ? AND month IS NOT NULL"
        elif cluster_id == -1:
            query = "SELECT DISTINCT month FROM media WHERE year = ? AND month IS NOT NULL AND file_path NOT IN (SELECT file_path FROM faces)"
        elif cluster_id == -2:
            query = "SELECT DISTINCT month FROM media WHERE year = ? AND month IS NOT NULL AND image_hash IN (SELECT image_hash FROM media GROUP BY image_hash HAVING COUNT(*) > 1)"
        else:
            query = "SELECT DISTINCT month FROM media m JOIN faces f ON m.file_path = f.file_path WHERE f.cluster_id = ? AND m.year = ? AND m.month IS NOT NULL"
            params = [cluster_id, year]
        with self.get_connection() as conn:
            cursor = conn.execute(query, params)
            return sorted([row[0] for row in cursor.fetchall()], reverse=True)

    def get_media_paged(self, cluster_id, year, month, limit=50, offset=0):
        query = "SELECT m.file_path, m.metadata_json, m.image_hash FROM media m"
        params = []
        where_clauses = []
        if cluster_id is not None:
            if cluster_id == -1:
                where_clauses.append("m.file_path NOT IN (SELECT file_path FROM faces)")
            elif cluster_id == -2:
                # Group duplicates by sorting by image_hash
                where_clauses.append("m.image_hash IN (SELECT image_hash FROM media GROUP BY image_hash HAVING COUNT(*) > 1)")
            else:
                query += " JOIN faces f ON m.file_path = f.file_path"
                where_clauses.append("f.cluster_id = ?")
                params.append(cluster_id)
        if year:
            where_clauses.append("m.year = ?")
            params.append(year)
        if month:
            where_clauses.append("m.month = ?")
            params.append(month)
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        
        # Ensure we sort by hash for duplicates to make grouping easy in the UI
        if cluster_id == -2:
            query += " ORDER BY m.image_hash, m.last_modified DESC LIMIT ? OFFSET ?"
        else:
            query += " ORDER BY m.last_modified DESC LIMIT ? OFFSET ?"
            
        params.extend([limit, offset])
        with self.get_connection() as conn:
            cursor = conn.execute(query, params)
            return [{"file_path": row[0], "metadata": json.loads(row[1]) if row[1] else {}, "group_hash": row[2]} for row in cursor.fetchall()]
