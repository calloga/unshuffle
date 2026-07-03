import unittest
import tempfile
from pathlib import Path
from unshuffle.core import get_file_hash
from unshuffle.core.hashing import (
    FAST_HASH_CHUNK_SIZE,
    FAST_HASH_PREFIX,
    get_fast_hash,
    hash_for_verification,
    is_fast_hash,
)


def _write_temp_file(data: bytes) -> Path:
    handle = tempfile.NamedTemporaryFile(delete=False)
    try:
        handle.write(data)
        return Path(handle.name)
    finally:
        handle.close()


class TestHashing(unittest.TestCase):
    def test_basic_hashing(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"hello world")
            f_path = Path(f.name)
        
        try:
            h1 = get_file_hash(f_path)
            h2 = get_file_hash(f_path)
            self.assertEqual(h1, h2)
            self.assertIsNotNone(h1)
            self.assertEqual(h1, "5eb63bbbe01eeed093cb22bb8f5acdc3")
        finally:
            f_path.unlink()

    def test_different_contents(self):
        with tempfile.NamedTemporaryFile(delete=False) as f1:
            f1.write(b"content 1")
            p1 = Path(f1.name)
        with tempfile.NamedTemporaryFile(delete=False) as f2:
            f2.write(b"content 2")
            p2 = Path(f2.name)
        
        try:
            h1 = get_file_hash(p1)
            h2 = get_file_hash(p2)
            self.assertNotEqual(h1, h2)
        finally:
            p1.unlink()
            p2.unlink()

    def test_interruption(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"some data to hash")
            f_path = Path(f.name)
        
        try:
            h = get_file_hash(f_path, interrupted_check=lambda: True)
            self.assertIsNone(h)
        finally:
            f_path.unlink()

    def test_file_not_found(self):
        h = get_file_hash(Path("non_existent_file.wav"))
        self.assertIsNone(h)

    def test_fast_hash_is_deterministic_and_prefixed(self):
        f_path = _write_temp_file(b"hello fast hash")

        try:
            h1 = get_fast_hash(f_path)
            h2 = get_fast_hash(f_path)
            self.assertIsNotNone(h1)
            fast_hash = h1
            self.assertEqual(h1, h2)
            self.assertTrue(is_fast_hash(fast_hash))
            self.assertTrue(fast_hash.startswith(FAST_HASH_PREFIX))
            self.assertEqual(len(fast_hash), len(FAST_HASH_PREFIX) + 32)
        finally:
            f_path.unlink()

    def test_fast_hash_includes_file_size(self):
        p1 = _write_temp_file(b"a" * FAST_HASH_CHUNK_SIZE)
        p2 = _write_temp_file(b"a" * (FAST_HASH_CHUNK_SIZE + 1))

        try:
            self.assertNotEqual(get_fast_hash(p1), get_fast_hash(p2))
        finally:
            p1.unlink()
            p2.unlink()

    def test_fast_hash_detects_sampled_regions_for_large_files(self):
        size = FAST_HASH_CHUNK_SIZE * 5
        base = bytearray(b"a" * size)
        first_changed = bytearray(base)
        middle_changed = bytearray(base)
        last_changed = bytearray(base)
        middle_offset = (size - FAST_HASH_CHUNK_SIZE) // 2

        first_changed[10] = ord("b")
        middle_changed[middle_offset + 10] = ord("b")
        last_changed[size - 10] = ord("b")

        paths = [_write_temp_file(bytes(data)) for data in (base, first_changed, middle_changed, last_changed)]

        try:
            base_hash = get_fast_hash(paths[0])
            self.assertNotEqual(base_hash, get_fast_hash(paths[1]))
            self.assertNotEqual(base_hash, get_fast_hash(paths[2]))
            self.assertNotEqual(base_hash, get_fast_hash(paths[3]))
        finally:
            for path in paths:
                path.unlink()

    def test_fast_hash_ignores_unsampled_middle_for_large_files(self):
        size = FAST_HASH_CHUNK_SIZE * 5
        left = bytearray(b"a" * size)
        right = bytearray(left)
        right[FAST_HASH_CHUNK_SIZE + 10] = ord("b")
        p1 = _write_temp_file(bytes(left))
        p2 = _write_temp_file(bytes(right))

        try:
            self.assertEqual(get_fast_hash(p1), get_fast_hash(p2))
            self.assertNotEqual(get_file_hash(p1), get_file_hash(p2))
        finally:
            p1.unlink()
            p2.unlink()

    def test_fast_hash_interruption(self):
        f_path = _write_temp_file(b"a" * (FAST_HASH_CHUNK_SIZE * 4))
        calls = 0

        def interrupted_after_first_check():
            nonlocal calls
            calls += 1
            return calls > 1

        try:
            self.assertIsNone(get_fast_hash(f_path, interrupted_check=interrupted_after_first_check))
        finally:
            f_path.unlink()

    def test_fast_hash_file_not_found(self):
        self.assertIsNone(get_fast_hash(Path("non_existent_file.wav")))

    def test_is_fast_hash_rejects_invalid_values(self):
        self.assertFalse(is_fast_hash(None))
        self.assertFalse(is_fast_hash("5eb63bbbe01eeed093cb22bb8f5acdc3"))
        self.assertFalse(is_fast_hash(f"{FAST_HASH_PREFIX}not-md5"))
        self.assertFalse(is_fast_hash(f"{FAST_HASH_PREFIX}{'g' * 32}"))

    def test_hash_for_verification_uses_expected_hash_kind(self):
        f_path = _write_temp_file(b"a" * (FAST_HASH_CHUNK_SIZE * 4))

        try:
            fast_hash = get_fast_hash(f_path)
            full_hash = get_file_hash(f_path)
            self.assertEqual(hash_for_verification(f_path, fast_hash), fast_hash)
            self.assertEqual(hash_for_verification(f_path, full_hash), full_hash)
        finally:
            f_path.unlink()

if __name__ == "__main__":
    unittest.main()
