from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import replace
from pathlib import Path

from ...persistence import get_global_system_dir
from .models import TreeOrganizationProfile, make_empty_profile, utc_now_iso


PROFILE_FILE = "tree_organization_profiles.json"


class TreeOrganizationProfileStoreError(RuntimeError):
    """Raised when the profile store cannot be read without risking data loss."""


class TreeOrganizationRepository:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else get_global_system_dir() / PROFILE_FILE

    def list_profiles(self) -> list[TreeOrganizationProfile]:
        payload = self._load_payload()
        profiles = []
        for item in payload.get("profiles", []):
            if isinstance(item, dict):
                try:
                    profile = TreeOrganizationProfile.from_dict(item)
                except (TypeError, ValueError):
                    continue
                if profile.id and profile.nodes:
                    profiles.append(profile)
        return profiles

    def get_profile(self, profile_id: str) -> TreeOrganizationProfile | None:
        for profile in self.list_profiles():
            if profile.id == profile_id:
                return profile
        return None

    def create_profile(self, name: str) -> TreeOrganizationProfile:
        profile = make_empty_profile(f"profile_{uuid.uuid4().hex[:12]}", (name or "Custom Tree"))
        self.save_profile(profile)
        return profile

    def save_profile(self, profile: TreeOrganizationProfile) -> TreeOrganizationProfile:
        now = utc_now_iso()
        updated = replace(profile, updated_at=now)
        profiles = [item for item in self.list_profiles() if item.id != updated.id]
        profiles.append(updated)
        profiles.sort(key=lambda item: item.name.lower())
        self._write_payload({"version": 1, "profiles": [item.to_dict() for item in profiles]})
        return updated

    def delete_profile(self, profile_id: str) -> None:
        profiles = [item for item in self.list_profiles() if item.id != profile_id]
        self._write_payload({"version": 1, "profiles": [item.to_dict() for item in profiles]})

    def duplicate_profile(self, profile_id: str, name: str | None = None) -> TreeOrganizationProfile | None:
        source = self.get_profile(profile_id)
        if source is None:
            return None
        now = utc_now_iso()
        duplicate = replace(
            source,
            id=f"profile_{uuid.uuid4().hex[:12]}",
            name=name or f"{source.name} Copy",
            created_at=now,
            updated_at=now,
        )
        return self.save_profile(duplicate)

    def _load_payload(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "profiles": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            self._backup_unreadable_store()
            raise TreeOrganizationProfileStoreError(
                f"Tree organization profile store is corrupt: {self.path}"
            ) from exc
        except OSError:
            return {"version": 1, "profiles": []}
        if not isinstance(payload, dict):
            self._backup_unreadable_store()
            raise TreeOrganizationProfileStoreError(
                f"Tree organization profile store is not an object: {self.path}"
            )
        if not isinstance(payload.get("profiles"), list):
            self._backup_unreadable_store()
            raise TreeOrganizationProfileStoreError(
                f"Tree organization profile store has invalid profiles payload: {self.path}"
            )
        return payload

    def _write_payload(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _backup_unreadable_store(self) -> Path | None:
        if not self.path.exists():
            return None
        backup = self.path.with_name(f"{self.path.name}.corrupt-{uuid.uuid4().hex[:8]}.bak")
        try:
            shutil.copy2(self.path, backup)
        except OSError:
            return None
        return backup
