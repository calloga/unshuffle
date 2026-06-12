from collections import Counter
from pathlib import Path
from unshuffle.core import stable_record_identity

class ReorgManager:
    """
    Manages the 'Draft Reorganization' state, including pending changes,
    collision detection, and impact analysis.
    """
    def __init__(self, key_factory=None):
        self.originals = {}
        self.non_learning_originals = set()
        self.conflicts = 0
        self.base_counts = {}
        self.current_counts = {}
        self.record_keys = {}
        self.collision_delta = 0
        self.collision_enabled = False
        self.key_factory = key_factory or self._default_key_factory

    def _default_key_factory(self, rec):
        audio_type = str(getattr(rec, "audio_type", "")).strip().lower()
        category = str(getattr(rec, "category", "")).strip().lower()
        subcategory = str(getattr(rec, "subcategory", "") or "").strip().lower()
        pack = str(getattr(rec, "pack", "")).strip().lower()
        filename = str(getattr(rec, "source_path", Path("")).name).strip().lower()
        return (audio_type, category, subcategory, pack, filename)

    def _record_id(self, rec):
        return stable_record_identity(rec)

    def clear(self):
        self.originals.clear()
        self.non_learning_originals.clear()
        self.conflicts = 0
        self.base_counts.clear()
        self.current_counts.clear()
        self.record_keys.clear()
        self.collision_delta = 0
        self.collision_enabled = False

    def init_counters(self, records):
        self.base_counts.clear()
        self.current_counts.clear()
        self.record_keys.clear()
        self.collision_delta = 0
        
        for rec in records:
            key = self.key_factory(rec)
            rec_id = self._record_id(rec)
            self.record_keys[rec_id] = key
            self.base_counts[key] = self.base_counts.get(key, 0) + 1
        
        self.current_counts = dict(self.base_counts)

    def stage_updates(self, updates, collision_check=False, learn=True):
        """
        updates: list of (record, column_index, old_value)
        """
        self.collision_enabled = self.collision_enabled or bool(collision_check)
        
        for rec, col, old_val in updates:
            key = (self._record_id(rec), col)
            if key not in self.originals:
                self.originals[key] = (rec, col, old_val)
            if learn:
                self.non_learning_originals.discard(key)
            else:
                self.non_learning_originals.add(key)

        if self.collision_enabled:
            touched = {self._record_id(r): r for r, c, v in updates}
            self._update_collision_delta(touched.values())
        
        self.conflicts = max(0, self.collision_delta) if self.collision_enabled else 0

    def _update_collision_delta(self, records):
        for rec in records:
            rec_id = self._record_id(rec)
            old_key = self.record_keys.get(rec_id)
            new_key = self.key_factory(rec)
            if old_key == new_key:
                continue
                
            if old_key is not None:
                self._apply_count_change(old_key, -1)
            self._apply_count_change(new_key, 1)
            self.record_keys[rec_id] = new_key

    def _apply_count_change(self, key, delta):
        old_current = self.current_counts.get(key, 0)
        base_count = self.base_counts.get(key, 0)
        
        def excess(count):
            return count - 1 if count > 1 else 0
            
        old_contrib = excess(old_current) - excess(base_count)
        
        new_current = old_current + delta
        if new_current <= 0:
            self.current_counts.pop(key, None)
            new_current = 0
        else:
            self.current_counts[key] = new_current
            
        new_contrib = excess(new_current) - excess(base_count)
        self.collision_delta += (new_contrib - old_contrib)

    def get_revert_list(self):
        return list(self.originals.values())

    def has_changes(self):
        return bool(self.originals)

    def should_learn(self, rec, col):
        key = (self._record_id(rec), col)
        return key not in self.non_learning_originals
