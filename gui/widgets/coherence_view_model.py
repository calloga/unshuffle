from __future__ import annotations

from dataclasses import dataclass

from unshuffle.logic.coherence.vector_index import records_from_staging_rows, valid_coherence_vector

from .coherence_math import _stable_hash, _vector_signature


@dataclass(frozen=True)
class AnalyzerPoint:
    record_id: str
    audio_type: str
    category: str
    subcategory: str
    cluster_id: str
    vector: list[float]
    source_path: str = ""


def coherence_points_from_app(app) -> tuple[list[AnalyzerPoint], list[dict]]:
    model = getattr(app, "model", None)
    engine = getattr(app, "engine", None)
    results = []
    result_by_id = {}
    if engine is not None and getattr(engine, "db", None) is not None and getattr(engine, "session_id", None):
        if hasattr(engine.db, "list_coherence_results"):
            results = list(engine.db.list_coherence_results(engine.session_id))
            result_by_id = {str(row.get("record_id") or ""): row for row in results}
    model_records = list(getattr(model, "records", []) or []) if model is not None else []
    if model_records:
        return analyzer_points_from_model_records(model_records, result_by_id), results
    if engine is None or getattr(engine, "db", None) is None or not getattr(engine, "session_id", None):
        return [], results
    if not hasattr(engine.db, "get_staging_records"):
        return [], results
    coherence_records, _stats = records_from_staging_rows(engine.db.get_staging_records(engine.session_id))
    return [
        AnalyzerPoint(
            record.record_id,
            record.audio_type,
            record.category,
            record.subcategory,
            str(result_by_id.get(record.record_id, {}).get("cluster_id") or f"{record.audio_type}:{record.category}:{record.subcategory}"),
            record.vector,
            record.source_path,
        )
        for record in coherence_records
    ], results


def analyzer_points_from_model_records(model_records: list, result_by_id: dict[str, dict]) -> list[AnalyzerPoint]:
    records: list[AnalyzerPoint] = []
    for row, rec in enumerate(model_records):
        category = str(getattr(rec, "category", "") or "").strip()
        if bool(getattr(rec, "is_preserved", False)) or category in {"Non-Audio Assets", "Metadata"}:
            continue
        vector = valid_coherence_vector(getattr(rec, "feature_vector", None) or getattr(rec, "acoustic_vector", None))
        if vector is None:
            continue
        record_id = str(getattr(rec, "staging_row_id", row) if getattr(rec, "staging_row_id", None) is not None else row)
        result = result_by_id.get(record_id, {})
        audio_type = str(getattr(rec, "audio_type", "") or "")
        subcategory = str(getattr(rec, "subcategory", "") or "")
        cluster_id = str(result.get("cluster_id") or f"{audio_type}:{category}:{subcategory}")
        records.append(
            AnalyzerPoint(
                record_id,
                audio_type,
                category,
                subcategory,
                cluster_id,
                vector,
                str(getattr(rec, "source_path", "") or ""),
            )
        )
    return records


def analyzer_data_key(app, records: list[AnalyzerPoint], results: list[dict]) -> tuple:
    engine = getattr(app, "engine", None)
    model = getattr(app, "model", None)
    session_id = getattr(engine, "session_id", None)
    model_records = getattr(model, "records", None)
    result_fingerprint = (
        len(results),
        sum(_stable_hash(str(row.get("record_id") or "")) for row in results),
        sum(_stable_hash(str(row.get("cluster_id") or "")) for row in results),
    )
    return (
        session_id,
        id(model),
        len(model_records or []),
        len(records),
        points_signature(records),
        result_fingerprint,
    )


def points_signature(points: list[AnalyzerPoint]) -> tuple:
    return (
        len(points),
        sum(_stable_hash(point.record_id) for point in points),
        sum(_stable_hash(point.audio_type) for point in points),
        sum(_stable_hash(point.category) for point in points),
        sum(_stable_hash(point.subcategory) for point in points),
        sum(_stable_hash(point.cluster_id) for point in points),
        sum(_vector_signature(point.vector) for point in points),
    )
