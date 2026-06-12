from pathlib import Path

from unshuffle.logic.classification import diagnose_file, format_file_diagnosis


def test_diagnose_file_reports_matching_tokens():
    diagnosis = diagnose_file(Path("Kicks/kick_loop.wav"), scan_root=Path("Kicks"))

    assert diagnosis.tokens
    assert any(entry.token == "kick" and entry.status == "matched" for entry in diagnosis.token_contributions)
    assert diagnosis.best_category != ""
    assert "Result:" in format_file_diagnosis(diagnosis)


def test_diagnose_file_marks_unknown_tokens_not_found():
    diagnosis = diagnose_file(Path("mysterytoken.wav"))

    assert any(entry.status == "not_found" for entry in diagnosis.token_contributions)


def test_diagnose_file_uses_component_trace_instead_of_rescoring():
    diagnosis = diagnose_file(Path("Kicks/kick_loop.wav"))

    assert any(entry.component == "filename" for entry in diagnosis.token_contributions if entry.status == "matched")
