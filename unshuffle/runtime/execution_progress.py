"""Progress and safety decision helpers for runtime execution."""

from typing import Any


def low_battery_emergency(psutil_module: Any) -> bool:
    battery = psutil_module.sensors_battery() if psutil_module else None
    return bool(battery and battery.percent < 5 and not battery.power_plugged)


def batch_log_message(index: int, total_files: int, source_name: str) -> str | None:
    if index % 50 == 0 or index == total_files:
        return f"Processing batch: {index}/{total_files} ({source_name})"
    return None


def progress_payload(index: int, total_files: int) -> dict[str, int] | None:
    if index % 10 == 0 or index == total_files:
        return {"current": index, "total": total_files}
    return None
