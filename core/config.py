from dataclasses import dataclass
from typing import Any

from .repositories.setting_repository import SettingRepository


@dataclass
class AppConfig:
    """
    Strongly typed application configuration.
    Loaded from SettingRepository on startup.
    """

    # Face Recognition
    face_det_thresh: float = 0.35
    face_min_samples: int = 2
    face_cluster_eps: float = 0.42
    face_merge_threshold: float = 0.55

    # Duplicate Detection
    threshold: float = 0.6  # Standard image similarity
    dup_threshold: float = 0.6  # Alias for threshold
    dup_threshold_stage2: float = 0.95  # Strict structural similarity

    # Video Similarity (relative to image threshold)
    video_threshold_ratio: float = 0.4 / 0.6

    force_reanalyze: bool = False
    include_trash: bool = False

    @classmethod
    def load(cls, repo: SettingRepository) -> "AppConfig":
        """Initializes a config object from the database."""
        settings = repo.get_all_settings()

        def to_bool(val: str) -> bool:
            return val.lower() == "true"

        def to_float(val: Any, default: float) -> float:
            try:
                f = float(val)
                # Handle legacy 100x integers if detected
                if f > 1.0 and default <= 1.0:
                    return f / 100.0
                return f
            except (ValueError, TypeError):
                return default

        return cls(
            face_det_thresh=to_float(settings.get("face_det_thresh"), 0.35),
            face_min_samples=int(settings.get("face_min_samples", 2)),
            face_cluster_eps=to_float(settings.get("face_cluster_eps"), 0.42),
            face_merge_threshold=to_float(settings.get("face_merge_threshold"), 0.55),
            threshold=to_float(settings.get("threshold"), 0.6),
            dup_threshold=to_float(settings.get("dup_threshold"), 0.6),
            dup_threshold_stage2=to_float(settings.get("dup_threshold_stage2"), 0.95),
            force_reanalyze=to_bool(settings.get("force_reanalyze", "False")),
            include_trash=to_bool(settings.get("include_trash", "False")),
        )

    def save(self, repo: SettingRepository) -> None:
        """Persists current config values to the database."""
        repo.save_setting("face_det_thresh", self.face_det_thresh)
        repo.save_setting("face_min_samples", self.face_min_samples)
        repo.save_setting("face_cluster_eps", self.face_cluster_eps)
        repo.save_setting("face_merge_threshold", self.face_merge_threshold)
        repo.save_setting("threshold", self.threshold)
        repo.save_setting("dup_threshold", self.dup_threshold)
        repo.save_setting("dup_threshold_stage2", self.dup_threshold_stage2)
        repo.save_setting("force_reanalyze", str(self.force_reanalyze))
        repo.save_setting("include_trash", str(self.include_trash))
