import unittest
from unittest import mock

from unshuffle import cli


class CliValidationTests(unittest.TestCase):
    def test_session_id_requires_undo(self):
        parser = mock.Mock()
        args = mock.Mock(
            no_prefix=False,
            flat=False,
            session_id="123",
            undo=False,
            source=None,
            pack_name=None,
            move=False,
            dry_run=False,
            rebuild_cache=False,
            force_cache_reset=False,
        )

        with self.assertRaisesRegex(SystemExit, "2"):
            parser.error.side_effect = SystemExit(2)
            cli._validate_args(parser, args)

    def test_no_prefix_requires_flat(self):
        parser = mock.Mock()
        args = mock.Mock(
            no_prefix=True,
            flat=False,
            session_id=None,
            undo=False,
            source=None,
            pack_name=None,
            move=False,
            dry_run=False,
            rebuild_cache=False,
            force_cache_reset=False,
        )

        with self.assertRaisesRegex(SystemExit, "2"):
            parser.error.side_effect = SystemExit(2)
            cli._validate_args(parser, args)

    def test_source_is_required_unless_only_cache_maintenance_is_requested(self):
        parser = mock.Mock()
        args = mock.Mock(
            no_prefix=False,
            flat=False,
            session_id=None,
            undo=False,
            source=None,
            pack_name=None,
            move=False,
            dry_run=False,
            rebuild_cache=False,
            force_cache_reset=False,
        )

        with self.assertRaisesRegex(SystemExit, "2"):
            parser.error.side_effect = SystemExit(2)
            cli._validate_args(parser, args)
