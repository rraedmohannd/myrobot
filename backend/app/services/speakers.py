"""Anonymous, in-memory speaker tracking for one conversation (docs/SPEAKERS.md).

Voice: CAM++ speaker embeddings via sherpa-onnx (local CPU, no internet).
Face: optional camera track id of the person whose lips moved (sent by the robot).
Nothing here is stored in the database except the resulting speaker number per turn.
"""

import logging
import math
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Protocol

from app.core.config import get_settings

log = logging.getLogger(__name__)


class SpeakerEmbedder(Protocol):
    available: bool

    def embed(self, pcm16: bytes, sample_rate: int) -> list[float] | None: ...


class NullSpeakerEmbedder:
    """Used when the model or library is missing: speaker tracking falls back to face/last."""

    available = False

    def embed(self, pcm16: bytes, sample_rate: int) -> list[float] | None:
        return None


class SherpaSpeakerEmbedder:
    available = True

    def __init__(self, model_path: str, min_seconds: float) -> None:
        import numpy as np  # noqa: F401  (fail early if missing)
        import sherpa_onnx

        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=model_path, num_threads=2, debug=False, provider="cpu"
        )
        if not config.validate():
            raise ValueError(f"invalid speaker model config: {model_path}")
        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        self.dim = self._extractor.dim
        self.min_seconds = min_seconds

    def embed(self, pcm16: bytes, sample_rate: int) -> list[float] | None:
        """Blocking (CPU): call through asyncio.to_thread."""
        import numpy as np

        if len(pcm16) < sample_rate * 2 * self.min_seconds:
            return None  # too short for a reliable voiceprint
        samples = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        stream = self._extractor.create_stream()
        stream.accept_waveform(sample_rate=sample_rate, waveform=samples)
        stream.input_finished()
        if not self._extractor.is_ready(stream):
            return None
        return list(self._extractor.compute(stream))


@lru_cache
def get_speaker_embedder() -> SpeakerEmbedder:
    s = get_settings()
    if not s.speaker_model_path or not os.path.exists(s.speaker_model_path):
        log.warning("speaker model not found (%s): voice speaker tracking disabled", s.speaker_model_path)
        return NullSpeakerEmbedder()
    try:
        return SherpaSpeakerEmbedder(s.speaker_model_path, s.speaker_min_seconds)
    except Exception:  # noqa: BLE001
        log.exception("could not load speaker model: voice speaker tracking disabled")
        return NullSpeakerEmbedder()


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


@dataclass
class Speaker:
    label: int
    centroid: list[float] | None = None
    n: int = 0
    face_tracks: set[int] = field(default_factory=set)

    def add_voice(self, emb: list[float]) -> None:
        norm = math.sqrt(sum(x * x for x in emb)) or 1.0
        unit = [x / norm for x in emb]
        if self.centroid is None:
            self.centroid = unit
        else:  # running mean of unit vectors
            self.centroid = [(c * self.n + u) / (self.n + 1) for c, u in zip(self.centroid, unit, strict=True)]
        self.n += 1


@dataclass
class Assignment:
    label: int
    is_new: bool
    confidence: float | None
    source: str  # voice | face | voice+face | last | first


class SpeakerTracker:
    """Decides 'Speaker N' for each utterance from voice similarity and/or the active face."""

    def __init__(self, threshold: float = 0.5, max_speakers: int = 4, face_margin: float = 0.15,
                 voice_override: float = 0.1) -> None:  # fmt: skip
        self.threshold = threshold
        self.max_speakers = max_speakers
        self.face_margin = face_margin  # face wins if its voice sim is within this of the threshold
        self.voice_override = voice_override  # a clearly different known voice beats the face
        self.speakers: list[Speaker] = []
        self.last_label: int | None = None

    def _new(self) -> Speaker:
        sp = Speaker(label=len(self.speakers) + 1)
        self.speakers.append(sp)
        return sp

    def _by_face(self, track: int | None) -> Speaker | None:
        if track is None:
            return None
        return next((s for s in self.speakers if track in s.face_tracks), None)

    def assign(self, emb: list[float] | None, face_track: int | None = None) -> Assignment:
        face_sp = self._by_face(face_track)
        sims = {s.label: cosine(emb, s.centroid) for s in self.speakers if emb and s.centroid}
        best_label = max(sims, key=sims.get) if sims else None
        best_sim = sims.get(best_label, -1.0) if best_label else -1.0

        chosen: Speaker | None = None
        is_new, source, conf = False, "voice", None

        if emb is None:
            if face_sp:
                chosen, source = face_sp, "face"
            elif face_track is not None and len(self.speakers) < self.max_speakers:
                chosen, is_new, source = self._new(), True, "face"
            elif self.speakers:
                chosen = next(s for s in self.speakers if s.label == (self.last_label or 1))
                source = "last"
            else:
                chosen, is_new, source = self._new(), True, "first"
        else:
            if face_sp is not None:
                face_sim = sims.get(face_sp.label)
                if face_sim is None or face_sim >= self.threshold - self.face_margin:
                    chosen, source, conf = face_sp, "voice+face" if face_sim is not None else "face", face_sim
                elif best_label is not None and best_sim >= self.threshold + self.voice_override:
                    chosen = next(s for s in self.speakers if s.label == best_label)
                    source, conf = "voice", best_sim
                else:
                    chosen, source, conf = face_sp, "face", face_sim
            elif best_label is not None and best_sim >= self.threshold:
                chosen = next(s for s in self.speakers if s.label == best_label)
                source, conf = "voice", best_sim
            elif len(self.speakers) < self.max_speakers:
                chosen, is_new = self._new(), True
                source = "voice" if self.speakers[:-1] else "first"
                conf = best_sim if best_label is not None else None
            else:
                chosen = next(s for s in self.speakers if s.label == best_label)
                source, conf = "voice", best_sim
            chosen.add_voice(emb)

        if face_track is not None:
            for s in self.speakers:  # a face belongs to one speaker only
                s.face_tracks.discard(face_track)
            chosen.face_tracks.add(face_track)
        self.last_label = chosen.label
        return Assignment(chosen.label, is_new, None if conf is None else round(conf, 3), source)
