import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("surge_metadata", Path(__file__).parents[1] / "surge_worker.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fxp(xml, structured=True):
    payload = b"sub3" + struct.pack("<7I", len(xml), 0, 0, 0, 0, 0, 0) + xml if structured else xml
    return (b"CcnK" + struct.pack(">I", 0) + b"FPCh" + struct.pack(">I", 1) + b"cjs3" +
            struct.pack(">II", 1, 1) + b"Header Name".ljust(28, b"\0") + struct.pack(">I", len(payload)) + payload)


class PatchMetadataTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "test.fxp"

    def test_legacy_header_fallback_and_stored_unicode_tags(self):
        self.path.write_bytes(fxp(b"<patch/>\0", structured=False))
        self.assertEqual(module.patch_metadata(self.path)["name"], "Header Name")
        self.path.write_bytes(fxp('<patch revision="31"><meta name="Bäss" category="Bass" author="Y"><tags><tag tag="Neuro"/></tags></meta></patch>'.encode()))
        metadata = module.patch_metadata(self.path)
        self.assertEqual(metadata["name"], "Bäss")
        self.assertEqual(metadata["tags"], ["Neuro"])
        self.path.write_bytes(fxp(b'<patch revision="31"><meta name="Readable"/><parameters><data value="&#0;"/></parameters></patch>'))
        self.assertEqual(module.patch_metadata(self.path)["name"], "Readable")

    def test_truncation_invalid_lengths_entities_and_wrong_plugin_are_rejected(self):
        valid = fxp(b'<patch><meta name="Test"/></patch>')
        malformed = [valid[:length] for length in [0, 4, 59, 60, 63, 91, len(valid) - 1]]
        malformed += [valid[:16] + b"ABCD" + valid[20:],
                      valid[:64] + struct.pack("<I", 2 ** 31) + valid[68:],
                      fxp(b'<!DOCTYPE patch [<!ENTITY x "expanded">]><patch><meta name="&x;"/></patch>')]
        for data in malformed:
            self.path.write_bytes(data)
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                module.patch_metadata(self.path)


if __name__ == "__main__":
    unittest.main()
