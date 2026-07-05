from __future__ import annotations

from unshuffle.core.progress import PhaseProgress, eta_seconds, format_eta, progress_message


def test_eta_seconds_uses_elapsed_rate():
    assert eta_seconds(50, 100, started_at=0.0, now=10.0) == 10
    assert eta_seconds(0, 100, started_at=0.0, now=10.0) is None
    assert eta_seconds(100, 100, started_at=0.0, now=10.0) is None


def test_progress_message_includes_phase_counts_remaining_and_eta():
    text = progress_message(
        {
            "phase": "Hashing",
            "message": "Hashing samples...",
            "current": 25,
            "total": 100,
            "eta_seconds": 65,
        }
    )

    assert text == "Hashing samples... - 25/100 - 75 remaining - ETA 1m 05s"


def test_phase_progress_emits_structured_payload_with_percent_and_eta():
    payloads = []
    progress = PhaseProgress(
        payloads.append,
        "Classifying Samples",
        total=10,
        message="Classifying samples...",
        update_every=1,
        min_interval_seconds=0,
    )
    progress.started_at -= 5

    progress.emit(5)

    assert payloads
    payload = payloads[-1]
    assert payload["phase"] == "Classifying Samples"
    assert payload["current"] == 5
    assert payload["total"] == 10
    assert payload["remaining"] == 5
    assert payload["percent"] == 50
    assert payload["eta_seconds"] is not None


def test_format_eta_handles_seconds_minutes_and_hours():
    assert format_eta(None) == ""
    assert format_eta(9) == "9s"
    assert format_eta(65) == "1m 05s"
    assert format_eta(3660) == "1h 01m"
