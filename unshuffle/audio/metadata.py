import logging
import wave
from pathlib import Path
from typing import Optional

try:
    import mutagen

    HAS_MUTAGEN = True
except ImportError:
    mutagen = None
    HAS_MUTAGEN = False


def get_audio_duration(path: Path) -> Optional[float]:
    """Universal helper to get audio duration via Mutagen or Wave."""
    if not path.exists():
        return None

    if path.name.startswith("._"):
        return None

    if HAS_MUTAGEN:
        try:
            file_loader = getattr(mutagen, "File", None)
            audio = file_loader(path) if callable(file_loader) else None
            if audio and audio.info:
                return getattr(audio.info, "length", None)
        except Exception as exc:
            logging.debug("Mutagen duration read failed for %s: %s", path, exc)
            pass

    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as file_handle:
                frames = file_handle.getnframes()
                rate = file_handle.getframerate()
                if rate > 0:
                    return frames / float(rate)
        except Exception as exc:
            logging.debug("Wave duration read failed for %s: %s", path, exc)
            pass

    return None
