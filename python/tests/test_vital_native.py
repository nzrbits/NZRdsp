"""Opt-in native spectral engine checks; no audio device or editor is opened."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import numpy as np
import soundfile as sf


EXECUTABLE = os.environ.get("NZRDSP_VITAL_NATIVE")


@unittest.skipUnless(EXECUTABLE, "set NZRDSP_VITAL_NATIVE to the built spectral renderer")
class NativeSpectralTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="spectral-ä-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.serial = 0

    def invoke(self, request, success=True):
        self.serial += 1
        file = self.root / f"request-{self.serial}.json"
        file.write_text(json.dumps(request), encoding="utf-8")
        completed = subprocess.run([EXECUTABLE, str(file)], capture_output=True, timeout=30)
        if not success:
            self.assertNotEqual(completed.returncode, 0, completed.stdout)
            return completed
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))
        return json.loads(completed.stdout.decode("utf-8").splitlines()[-1])

    def render(self, name, **options):
        output = self.root / name
        request = dict(action="render", output=str(output), sample_rate=48000,
                       note=69, velocity=100, duration_frames=24001, tail_frames=24003)
        request.update(options)
        result = self.invoke(request)
        audio, rate = sf.read(output, dtype="float32", always_2d=True)
        self.assertEqual(rate, request["sample_rate"])
        self.assertEqual(audio.shape, (request["duration_frames"] + request["tail_frames"], 2))
        self.assertTrue(np.isfinite(audio).all())
        self.assertEqual(sf.info(output).subtype, "FLOAT")
        self.assertEqual(result["frames"], len(audio))
        self.assertEqual(result["maximum_block_size"], 64)
        self.assertEqual(result["peak"], float(np.max(np.abs(audio))))
        self.assertAlmostEqual(result["rms"], float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))), places=12)
        if request["duration_frames"] > 256:
            self.assertGreater(result["peak"], 0.001)
        return audio, output, result

    def test_native_parameters_presets_edits_and_seeded_audio_round_trip(self):
        metadata = self.invoke(dict(action="parameters"))
        self.assertEqual(metadata["source_commit"], "636ca0ef517a4db087a6a08a6a8a5e704e21f836")
        self.assertEqual(metadata["engine_version"], "1.0.6")
        self.assertEqual(metadata["parameter_count"], 772)
        changes = {"osc_1_transpose": 12, "osc_2_on": 1, "filter_1_on": 1, "filter_1_cutoff": 70}
        preset = self.root / "edited.vital"
        self.invoke(dict(action="save", output=str(preset), parameters=changes))
        loaded = self.invoke(dict(action="parameters", preset=str(preset)))
        for key, value in changes.items():
            self.assertEqual(loaded["parameters"][key]["value"], value)
        direct, first, _ = self.render("direct.wav", parameters=changes, seed=17)
        restored, second, _ = self.render("restored.wav", preset=str(preset), seed=17)
        self.assertTrue(np.array_equal(direct, restored))
        self.assertEqual(first.read_bytes(), second.read_bytes())
        repeat, third, _ = self.render("repeat.wav", parameters=changes, seed=17)
        self.assertTrue(np.array_equal(direct, repeat))
        self.assertEqual(first.read_bytes(), third.read_bytes())
        another_seed, _, _ = self.render("different-seed.wav", parameters=changes, seed=18)
        self.assertGreater(float(np.max(np.abs(direct - another_seed))), 0.001)
        defaults, _, _ = self.render("default.wav", seed=17)
        self.assertGreater(float(np.max(np.abs(defaults - direct))), 0.001)

    def test_rates_exact_partial_lengths_note_timing_and_pitch(self):
        for rate in (8000, 22050, 44100, 48000, 96000, 192000):
            with self.subTest(rate=rate):
                duration = rate // 2 + 1
                audio, _, _ = self.render(f"rate-{rate}.wav", sample_rate=rate,
                    duration_frames=duration, tail_frames=rate + 3,
                    parameters={"osc_1_random_phase": 0})
                self.assertGreater(float(np.sqrt(np.mean(audio[rate//10:rate//3] ** 2))), 0.001)
                self.assertLess(float(np.max(np.abs(audio[-rate//10:]))), 0.00001)
        lower, _, _ = self.render("lower.wav", parameters={"osc_1_random_phase": 0})
        upper, _, _ = self.render("upper.wav", note=81, parameters={"osc_1_random_phase": 0})
        def peak_frequency(audio):
            window = audio[4096:20480, 0].astype(np.float64) * np.hanning(16384)
            return int(np.argmax(np.abs(np.fft.rfft(window)))) * 48000 / len(window)
        self.assertAlmostEqual(peak_frequency(lower), 440, delta=4)
        self.assertAlmostEqual(peak_frequency(upper), 880, delta=4)
        one, _, _ = self.render("one.wav", duration_frames=1, tail_frames=0)
        self.assertEqual(len(one), 1)

    def test_note_off_preserves_prefix_and_releases_across_quantum_boundaries(self):
        for duration in (1, 63, 64, 65, 127, 128, 129):
            with self.subTest(duration=duration):
                held, _, _ = self.render(f"held-{duration}.wav", duration_frames=duration, tail_frames=0)
                audio, _, _ = self.render(f"off-{duration}.wav", duration_frames=duration, tail_frames=24000)
                self.assertTrue(np.array_equal(audio[:duration], held))
                self.assertLess(float(np.max(np.abs(audio[-4096:]))), 0.00001)

    def test_invalid_requests_and_future_presets_preserve_files(self):
        output = self.root / "output.wav"
        output.write_bytes(b"keep output")
        self.invoke(dict(action="render", output=str(output)), success=False)
        self.assertEqual(output.read_bytes(), b"keep output")
        preset = self.root / "preset.vital"
        self.invoke(dict(action="save", output=str(preset)))
        original = preset.read_bytes()
        self.invoke(dict(action="save", output=str(preset)), success=False)
        self.assertEqual(preset.read_bytes(), original)
        state = json.loads(original)
        self.assertEqual(state["synth_version"], "1.0.6")
        state["synth_version"] = "99.0.0"
        future = self.root / "future.vital"
        future.write_text(json.dumps(state), encoding="utf-8")
        malformed = self.root / "malformed.vital"
        malformed.write_text('{"settings":', encoding="utf-8")
        invalid = [dict(note=128), dict(velocity=0), dict(sample_rate=7999), dict(sample_rate=192001),
            dict(duration_frames=0), dict(tail_frames=-1), dict(seed=1000000001), dict(bpm=0),
            dict(parameters={"not_a_parameter": 0}), dict(parameters={"osc_1_on": 0.5}),
            dict(parameters={"osc_1_transpose": 1000}), dict(parameters={"osc_1_on": "1"}),
            dict(preset=str(future)), dict(preset=str(malformed)), dict(preset="relative.vital"),
            dict(output="relative.wav"), dict(output="D:/invalid\u0000name.wav"), dict(misspelled=True)]
        for index, fields in enumerate(invalid):
            with self.subTest(fields=fields):
                destination = self.root / f"invalid-{index}.wav"
                request = dict(action="render", output=str(destination))
                request.update(fields)
                self.invoke(request, success=False)
                self.assertFalse(destination.exists())
        self.assertEqual(output.read_bytes(), b"keep output")
