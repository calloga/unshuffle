def csv_record_row(record, dest_path, dest_folder) -> dict:
    return {
        "sample_name": dest_path.name,
        "source_filename": record.source_path.name,
        "audio_type": str(record.audio_type),
        "category": str(record.category),
        "subcategory": str(record.subcategory or ""),
        "pack": str(record.pack),
        "source_directory": str(record.source_path.parent),
        "target_directory": str(dest_folder),
        "confidence_level": record.confidence,
        "tags": ",".join(getattr(record, "tags", [])),
    }

