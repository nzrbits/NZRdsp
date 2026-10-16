"""Opt-in internal Dexed backend checks; no editor, playback or MIDI devices."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import numpy as np
import soundfile as sf

NATIVE = os.environ.get("NZRDSP_DEXED_NATIVE")
PIN = "fea2f0f0f2c397799f59b27eb11c038cd34b22c5"


def voice(coarse=1):
    data = bytearray(155)
    for op in range(6):
        start = op * 21
        data[start:start + 8] = bytes([99, 99, 99, 99, 99, 99, 99, 0])
        data[start + 18] = 1
        data[start + 20] = 7
    data[121] = 99
    data[123] = coarse
    data[126:134] = bytes([99] * 4 + [50] * 4)
    data[134] = 31
    data[136] = 1
    data[144] = 24
    data[145:155] = b"NZR TEST  "
    return bytes([240, 67, 0, 0, 1, 27]) + data + bytes([(-sum(data)) & 127, 247])


@unittest.skipUnless(NATIVE, "set NZRDSP_DEXED_NATIVE to the internal native renderer")
class DexedNative(unittest.TestCase):
    def render(self, source, target, *, rate=48000, duration=12000, tail=24000, block=512, engine=1, note=60, success=True):
        result = subprocess.run([NATIVE, str(source), str(target), str(note), "100", str(duration), str(tail), str(rate), str(block), str(engine), "0"], capture_output=True, text=True, encoding="utf-8", timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode == 0, success, result.stderr)
        if not success:
            return result
        metadata = json.loads(result.stdout.splitlines()[-1])
        info = sf.info(target)
        audio, actual_rate = sf.read(target, dtype="float32", always_2d=True)
        self.assertEqual((info.frames, actual_rate, info.channels, info.subtype), (duration + tail, rate, 2, "FLOAT"))
        self.assertEqual(metadata["frames"], duration + tail)
        self.assertEqual(metadata["source_commit"], PIN)
        self.assertEqual((metadata["duration_frames"], metadata["tail_frames"], metadata["block"], metadata["note"], metadata["velocity"], metadata["native_quantum"]), (duration, tail, block, note, 100, 64))
        self.assertTrue(np.isfinite(audio).all())
        self.assertAlmostEqual(metadata["peak"], float(np.max(np.abs(audio))), places=14)
        self.assertAlmostEqual(metadata["rms"], float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))), places=12)
        return audio

    def test_all_engines_rates_partial_blocks_and_exact_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "voice.syx"
            source.write_bytes(voice())
            for engine in range(3):
                for rate in [22050, 44100, 48000, 96000, 192000]:
                    target = root / f"e{engine}-r{rate}.wav"
                    audio = self.render(source, target, rate=rate, duration=rate // 5 + 3, tail=rate // 2 + 7, block=127, engine=engine)
                    self.assertGreater(float(np.max(np.abs(audio[:rate // 5]))), 0.001)
                    self.assertLess(float(np.max(np.abs(audio[-rate // 10:]))), 1e-7)
                first = root / f"first{engine}.wav"
                second = root / f"second{engine}.wav"
                self.render(source, first, engine=engine)
                self.render(source, second, engine=engine)
                self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_native_program_frequency_changes_and_output_is_stereo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            peaks = []
            for coarse in [1, 2]:
                source = root / f"v{coarse}.syx"
                source.write_bytes(voice(coarse))
                audio = self.render(source, root / f"v{coarse}.wav", duration=48000, tail=0, block=64)
                np.testing.assert_array_equal(audio[:, 0], audio[:, 1])
                spectrum = np.abs(np.fft.rfft(audio[1000:, 0] * np.hanning(len(audio) - 1000)))
                peaks.append(np.argmax(spectrum) * 48000 / (len(audio) - 1000))
            self.assertAlmostEqual(peaks[0], 261.625565, delta=2)
            self.assertAlmostEqual(peaks[1], 523.251131, delta=2)

    def test_failures_preserve_input_existing_destination_and_leave_no_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "voice.syx"
            source.write_bytes(voice())
            original = source.read_bytes()
            target = root / "keep.wav"
            target.write_bytes(b"existing audio")
            self.render(source, target, success=False)
            self.assertEqual(target.read_bytes(), b"existing audio")
            never = root / "never.wav"
            self.render(source, never, note=128, success=False)
            self.assertFalse(never.exists())
            self.render(source, never, rate=8000, success=False)
            self.assertFalse(never.exists())
            self.assertEqual(source.read_bytes(), original)
            source.write_bytes(original[:-1])
            self.render(source, never, success=False)
            self.assertFalse(never.exists())
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["keep.wav", "voice.syx"])


if __name__ == "__main__":
    unittest.main()
