import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import test_driver as fixture

from synthDrivers.phononLocal import _imports


class ImportStoreTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name) / "profile" / "phononLocalVoices"
        self.source = Path(folder.name) / "French.safetensors"
        self.source.write_bytes(b"voice bytes")

    def test_import_persists_without_changing_original_and_can_be_removed(self):
        imported = _imports.add_voice(self.root, self.source, " French ", "fr_FR",
                                      lambda data: self.assertEqual(data, b"voice bytes"))
        self.assertEqual(_imports.list_voices(self.root), (imported,))
        self.assertEqual(imported.name, "French")
        self.assertEqual(_imports.read_voice(self.root, imported.id), b"voice bytes")
        _imports.remove_voice(self.root, imported.id)
        self.assertEqual(tuple(self.root.iterdir()), ())
        self.assertEqual(self.source.read_bytes(), b"voice bytes")

    def test_failed_validation_and_invalid_names_never_publish_a_voice(self):
        def reject(_):
            raise ValueError("invalid tensor")
        with self.assertRaisesRegex(ValueError, "invalid tensor"):
            _imports.add_voice(self.root, self.source, "French", "fr_FR", reject)
        with self.assertRaises(ValueError):
            _imports.add_voice(self.root, self.source, "Bad\nName", "fr_FR", lambda _: None)
        self.assertFalse(self.root.exists())

    def test_invalid_id_cannot_remove_other_profile_data(self):
        self.root.mkdir(parents=True)
        with self.assertRaises(ValueError):
            _imports.remove_voice(self.root, "..")
        self.assertTrue(self.source.exists())

    def test_oversized_file_is_rejected_before_native_validation(self):
        self.source.write_bytes(b"x" * 5)
        with patch.object(_imports, "MAX_BYTES", 4), self.assertRaises(ValueError):
            _imports.add_voice(self.root, self.source, "French", "fr_FR", lambda _: self.fail("validation called"))
