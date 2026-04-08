import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class FaceCountsResult:
    unknown: int
    ignored: int
    persons: dict[int, int]


@dataclass
class LibraryViewItem:
    media: "MediaRecord"
    ui_group_id: Optional[int] = None
    selected: bool = False

    @property
    def is_header(self) -> bool:
        return False

    @property
    def file_path(self) -> str:
        return self.media.file_path


@dataclass
class LibraryViewHeader:
    is_header: bool = True
    date_header: str = ""
    location_header: str = ""
    suggestion_label: str = ""  # For AI suggestion groupings
    ui_group_id: Optional[int] = None

    @property
    def file_path(self) -> str:
        return ""


@dataclass
class FaceDisplayItem:
    face: "FaceInfo"
    image: Any = None
    selected: bool = False

    @property
    def is_header(self) -> bool:
        return False

    @property
    def file_path(self) -> str:
        return self.face.file_path


@dataclass(frozen=True)
class FaceInfo:
    """Represents a single face record from the database."""

    face_id: int
    file_path: str
    bbox: Optional[list[float]] = None
    cluster_id: Optional[int] = None
    is_ignored: bool = False
    vector_blob: Optional[bytes] = None
    capture_date: Optional[str] = None

    frame_index: int = 0
    similarity: Optional[float] = None
    distance: Optional[float] = None
    metadata: Optional[dict[str, Any]] = None
    suggestion_type: Optional[str] = None
    suggestion_label: Optional[str] = None

    @classmethod
    def from_db_row(cls, row: tuple[Any, ...]) -> "FaceInfo":
        """From standard faces table query (7 columns)."""
        return cls(
            face_id=row[0],
            file_path=row[1],
            bbox=json.loads(row[2]) if row[2] else None,
            cluster_id=row[3],
            is_ignored=bool(row[4]),
            capture_date=row[5] if len(row) > 5 else None,
            frame_index=row[6] if len(row) > 6 else 0,
        )

    @classmethod
    def from_extended_row(cls, row: tuple[Any, ...]) -> "FaceInfo":
        """From query including metadata (5 columns)."""
        return cls(
            face_id=row[0],
            file_path=row[1],
            bbox=json.loads(row[2]) if row[2] else None,
            metadata=json.loads(row[3]) if row[3] else {},
            frame_index=row[4],
        )

    @classmethod
    def from_cluster_update_row(cls, row: tuple[Any, ...]) -> "FaceInfo":
        """From light query (4 columns usually)."""
        return cls(face_id=row[0], file_path=row[1], cluster_id=row[3] if len(row) > 3 else None)


@dataclass(frozen=True)
class MediaRecord:
    """Represents a media file entry from the database with optional joined fields."""

    file_path: str
    last_modified: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    group_id: Optional[str] = None
    location_id: Optional[int] = None
    thumbnail_path: Optional[str] = None
    is_corrupted: bool = False
    is_in_trash: bool = False
    capture_date: Optional[str] = None
    file_hash: Optional[str] = None
    year: Optional[int] = None
    month: Optional[int] = None

    # Extended fields from JOINs
    country: Optional[str] = None
    prefecture: Optional[str] = None
    city: Optional[str] = None
    vector_blob: Optional[bytes] = None
    salient_blob: Optional[bytes] = None
    is_duplicate: bool = False
    person_tags: Optional[str] = None
    discovery_method: Optional[str] = None

    @classmethod
    def from_media_table(cls, row: tuple[Any, ...]) -> "MediaRecord":
        """Creates a MediaRecord from standard media table row (12 columns)."""
        return cls(
            file_path=row[0],
            last_modified=row[1],
            metadata=json.loads(row[2]) if row[2] else {},
            group_id=row[3],
            location_id=row[4],
            thumbnail_path=row[5],
            is_corrupted=bool(row[6]),
            is_in_trash=bool(row[7]),
            capture_date=row[8],
            file_hash=row[9],
            year=row[10],
            month=row[11],
        )

    @classmethod
    def from_full_join(cls, row: tuple[Any, ...]) -> "MediaRecord":
        """Creates a MediaRecord from get_media / get_duplicates (18 columns).
        Columns: path, modified, meta, group_id, lat, lon, alt, country, pref, city, yr, mo, thumb, corrupt, trash, date, hash, vec
        """
        return cls(
            file_path=row[0],
            last_modified=row[1],
            metadata=json.loads(row[2]) if row[2] else {},
            group_id=row[3],
            # Skipping 4,5,6 (lat/lon/alt placeholders)
            country=row[7],
            prefecture=row[8],
            city=row[9],
            year=int(row[10]) if row[10] else None,
            month=int(row[11]) if row[11] else None,
            thumbnail_path=row[12],
            is_corrupted=bool(row[13]),
            is_in_trash=bool(row[14]),
            capture_date=row[15],
            file_hash=row[16],
            vector_blob=row[17],
        )

    @classmethod
    def from_paged_list(cls, row: tuple[Any, ...]) -> "MediaRecord":
        """Creates a MediaRecord from a paged view result (12 columns, different order).
        Columns: path, meta, group_id, is_in_trash, is_dupe, person_tags, thumb, discovery, city, pref, country, date
        """
        return cls(
            file_path=row[0],
            metadata=json.loads(row[1]) if row[1] else {},
            group_id=row[2],
            is_in_trash=bool(row[3]),
            is_duplicate=bool(row[4]),
            person_tags=row[5],
            thumbnail_path=row[6],
            discovery_method=row[7],
            city=row[8],
            prefecture=row[9],
            country=row[10],
            capture_date=row[11],
        )

    @classmethod
    def from_duplicate_search(cls, row: tuple[Any, ...]) -> "MediaRecord":
        """Creates a MediaRecord from duplicate search raw (9 columns).
        Columns: path, group_id, meta, is_in_trash, file_hash, cap_date, vec, salient, discovery
        """
        return cls(
            file_path=row[0],
            group_id=row[1],
            metadata=json.loads(row[2]) if row[2] else {},
            is_in_trash=bool(row[3]),
            file_hash=row[4],
            capture_date=row[5],
            vector_blob=row[6],
            salient_blob=row[7],
            discovery_method=row[8],
        )


@dataclass(frozen=True)
class ClusterInfo:
    """Represents a person/cluster from the database."""

    cluster_id: int
    custom_name: Optional[str] = None
    is_ignored: bool = False
    face_count: int = 0  # Number of unique photos containing this person

    def __iter__(self) -> Iterable[Any]:
        """Allows unpacking as (cluster_id, custom_name)."""
        return iter((self.cluster_id, self.custom_name))

    @classmethod
    def from_cluster_row(cls, row: tuple[Any, ...]) -> "ClusterInfo":
        """Expects: (cluster_id, custom_name, is_ignored, face_count)"""
        return cls(
            cluster_id=row[0],
            custom_name=row[1],
            is_ignored=bool(row[2]),
            face_count=row[3] if len(row) > 3 else 0,
        )


@dataclass(frozen=True)
class YearCount:
    year: int
    count: int


@dataclass(frozen=True)
class MonthCount:
    month: int
    count: int


@dataclass(frozen=True)
class LocationCount:
    city: str
    count: int


@dataclass(frozen=True)
class DuplicateStats:
    group_count: int
    file_count: int


@dataclass(frozen=True)
class RootCategoryCounts:
    total: int
    no_faces: int
    duplicates: int
    corrupted: int
