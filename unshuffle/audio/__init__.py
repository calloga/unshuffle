"""Audio-layer services.

Start here for:
- acoustic similarity / feature extraction: `SimilarityEngine`
- audio duration lookup: `get_audio_duration`
"""

from .acoustic import FeaturePayload, SimilarityEngine
from .metadata import get_audio_duration

__all__ = ["FeaturePayload", "SimilarityEngine", "get_audio_duration"]
