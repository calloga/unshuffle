import unittest
import sys
import os

class TestImports(unittest.TestCase):
    """
    Sanity check to ensure all core modules can be imported without errors.
    This helps catch circular dependencies and missing dependencies early.
    """

    def test_import_unshuffle_core(self):
        try:
            import unshuffle
            from unshuffle import APP_VERSION
            from unshuffle.core import models
            from unshuffle.logic import planning
        except ImportError as e:
            self.fail(f"Failed to import core unshuffle modules: {e}")

    def test_root_package_exposes_only_v1_release_constants(self):
        import unshuffle

        self.assertEqual(unshuffle.APP_VERSION, "1.0.0")
        self.assertFalse(hasattr(unshuffle, "Unshuffler"))
        self.assertFalse(hasattr(unshuffle, "run_plan"))
        self.assertFalse(hasattr(unshuffle, "setup_logging"))

    def test_import_gui(self):
        try:
            if not hasattr(sys, 'argv'):
                sys.argv = ['']
            
            import gui
            from gui.main import window, launcher
            from gui.core import worker_manager
            from gui.models import staging_table
        except ImportError as e:
            self.fail(f"Failed to import GUI modules: {e}")
        except Exception as e:
            print(f"Non-ImportError during GUI import (expected in headless env): {e}")

    def test_import_extractor_bridge(self):
        try:
            from unshuffle.audio import SimilarityEngine
        except ImportError as e:
            self.fail(f"Failed to import SimilarityEngine: {e}")

    def test_no_circular_dependencies(self):
        try:
            import unshuffle.runtime.engine
            import unshuffle.persistence
            from unshuffle.persistence import UnshuffleDB
        except ImportError as e:
            self.fail(f"Circular dependency or import error detected: {e}")

if __name__ == "__main__":
    unittest.main()
