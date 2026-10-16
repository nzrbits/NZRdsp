"""Synthetic SysEx CLI checks; no presets, plugins or audio devices required."""
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
import unittest

CLI = os.environ.get("NZRDSP_NATIVE_CLI")
ORACLE = os.environ.get("NZRDSP_DEXED_ORACLE")


def message(payload, format_byte, channel=3):
    size = len(payload)
    return bytes([0xF0, 0x43, channel, format_byte, size >> 7, size & 127]) + bytes(payload) + bytes([(-sum(payload)) & 127, 0xF7])


def bank(seed=6137):
    rng = random.Random(seed)
    payload = bytearray()
    for voice_index in range(32):
        voice = bytearray()
        for operator in range(6):
            voice.extend(rng.randrange(100) for _ in range(11))
            voice.extend([rng.randrange(128), rng.randrange(15) << 3 | rng.randrange(8),
                          rng.randrange(128), rng.randrange(100), rng.randrange(128), rng.randrange(100)])
        voice.extend(rng.randrange(100) for _ in range(8))
        voice.extend([rng.randrange(32), rng.randrange(128)])
        voice.extend(rng.randrange(100) for _ in range(4))
        voice.extend([rng.randrange(8) << 4 | rng.randrange(6) << 1 | rng.randrange(2), rng.randrange(49)])
        voice.extend(f"VOICE {voice_index:02d}  ".encode("ascii"))
        assert len(voice) == 128
        payload.extend(voice)
    return message(payload, 9)


@unittest.skipUnless(CLI, "set NZRDSP_NATIVE_CLI to the built nzrdsp executable")
class DexedSysEx(unittest.TestCase):
    def call(self, *args, success=True):
        result = subprocess.run([CLI, "dexed", *map(str, args)], capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode == 0, success, result.stderr)
        return json.loads(result.stdout) if success else result

    def test_bank_selection_edits_exports_and_schema(self):
        schema = self.call("--list-params")
        self.assertEqual(len(schema["parameters"]), 155)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bank.syx"
            original = bank()
            source.write_bytes(original)
            catalog = self.call("--sysex", source, "--list-voices")
            self.assertEqual(len(catalog["voices"]), 32)
            self.assertEqual(catalog["voices"][31]["name"], "VOICE 31")
            target = root / "edited.syx"
            result = self.call("--sysex", source, "--voice", 31, "--param", "algorithm=31", "--param", "op1.output_level=99", "--name", "NEURO", "--save-sysex", target)
            exported = self.call("--sysex", target)
            self.assertEqual(result["program_bytes"], exported["program_bytes"])
            self.assertEqual(exported["algorithm_display"], 32)
            self.assertEqual(exported["midi_channel"], 4)
            self.assertEqual(exported["name"], "NEURO")
            self.assertEqual(source.read_bytes(), original)
            before = target.read_bytes()
            self.call("--sysex", source, "--save-sysex", target, success=False)
            self.assertEqual(target.read_bytes(), before)
            self.call("--sysex", target, "--voice", 1, success=False)

    def test_invalid_files_and_edits_never_publish_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.syx"
            output = root / "never.syx"
            valid = bank()
            for broken in [valid[:-1], valid + b"extra", valid[:20] + b"\xff" + valid[21:], b"not sysex", valid * 100]:
                source.write_bytes(broken)
                self.call("--sysex", source, "--save-sysex", output, success=False)
                self.assertFalse(output.exists())
            source.write_bytes(valid)
            for invalid in ["algorithm=32", "unknown=1", "op1.detune=15"]:
                self.call("--sysex", source, "--param", invalid, "--save-sysex", output, success=False)
                self.assertFalse(output.exists())
            self.assertEqual(source.read_bytes(), valid)

    @unittest.skipUnless(ORACLE, "set NZRDSP_DEXED_ORACLE to the compiled pinned upstream unpackProgram harness")
    def test_all_bytes_against_pinned_upstream_cpp(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic.syx"
            comparisons = 0
            for seed in [6137, 20261016, 155]:
                source.write_bytes(bank(seed))
                for voice in range(32):
                    reference = subprocess.run([ORACLE, str(source), str(voice)], capture_output=True, text=True, check=True, timeout=10)
                    expected = list(map(int, reference.stdout.split()))
                    self.assertEqual(len(expected), 155)
                    actual = self.call("--sysex", source, "--voice", voice)["program_bytes"]
                    self.assertEqual(actual, expected, (seed, voice))
                    comparisons += 155
            print(json.dumps({"dexed_compared_bytes": comparisons, "voices": 96, "oracle_sha256": hashlib.sha256(Path(ORACLE).read_bytes()).hexdigest()}))


if __name__ == "__main__":
    unittest.main()
