import unittest
import tempfile
from pathlib import Path
from unshuffle.core import get_file_hash

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

if __name__ == "__main__":
    unittest.main()
