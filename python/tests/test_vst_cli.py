"""Opt-in tests with installed licensed plugins; no editor or device playback."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import numpy as np
import soundfile as sf


@unittest.skipUnless(os.environ.get("NZRDSP_NATIVE_CLI") and os.environ.get("NZRDSP_VST_TEST_PLUGINS"),
                     "Native VST fixtures not configured")
class NativeVstTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        cls.cli = os.environ["NZRDSP_NATIVE_CLI"]
        cls.runtime = os.environ["NZRDSP_RUNTIME"]
        cls.plugins = json.loads(os.environ["NZRDSP_VST_TEST_PLUGINS"])
        x = np.arange(12000) / 48000
        cls.audio = np.column_stack([.15 * np.sin(2 * np.pi * 440 * x),
                                     .1 * np.sin(2 * np.pi * 137 * x)]).astype("float32")
        cls.source = cls.root / "input.wav"
        sf.write(cls.source, cls.audio, 48000, subtype="FLOAT")
        cls.presets = {}
        for index, plugin in enumerate(cls.plugins):
            preset = cls.root / f"state-{index}.vstpreset"
            cls.invoke(plugin, "--save-preset", preset)
            cls.presets[plugin] = preset

    @classmethod
    def invoke(cls, plugin, *arguments, success=True):
        result = subprocess.run([cls.cli, "vst", "--runtime", cls.runtime, "--plugin", plugin,
                                 *map(str, arguments)], capture_output=True, text=True,
                                encoding="utf-8", timeout=125)
        if (result.returncode == 0) != success:
            raise AssertionError(result.stdout + result.stderr)
        return json.loads(result.stdout) if success else result.stderr

    def test_native_processing_matches_direct_host_and_preserves_state(self):
        from pedalboard import load_plugin
        for index, plugin_path in enumerate(self.plugins):
            with self.subTest(plugin=plugin_path):
                preset = self.presets[plugin_path]
                before = preset.read_bytes()
                output = self.root / f"processed-{index}.wav"
                manifest = self.invoke(plugin_path, "--preset", preset, "-i", self.source,
                                       "-o", output, "--tail", .05)
                actual, rate = sf.read(output, dtype="float32", always_2d=True)
                self.assertEqual((actual.shape, rate), ((14400, 2), 48000))
                self.assertTrue(np.isfinite(actual).all())
                self.assertGreater(manifest["peak"], .01)
                self.assertEqual(manifest["frames"], len(actual))
                self.assertEqual(preset.read_bytes(), before)
                self.assertEqual(json.loads(output.with_suffix(".wav.json").read_text()), manifest)
                native = load_plugin(plugin_path, initialization_timeout=10)
                native.load_preset(str(preset))
                native.reset()
                reference_input = np.concatenate([self.audio, np.zeros((2400, 2), dtype="float32")])
                expected = native.process(reference_input.T.copy(), 48000, buffer_size=512, reset=False).T
                np.testing.assert_allclose(actual, expected, atol=1e-6, rtol=0)

    def test_bypass_preserves_samples_and_silent_tail(self):
        for index, plugin in enumerate(self.plugins):
            with self.subTest(plugin=plugin):
                output = self.root / f"bypassed-{index}.wav"
                self.invoke(plugin, "--preset", self.presets[plugin], "-i", self.source,
                            "-o", output, "--tail", .05, "--param", "bypass=1")
                actual, _ = sf.read(output, dtype="float32", always_2d=True)
                np.testing.assert_array_equal(actual[:12000], self.audio)
                self.assertEqual(float(np.max(np.abs(actual[12000:]))), 0)

    def test_existing_outputs_and_invalid_assignments_are_safe(self):
        plugin = self.plugins[0]
        output = self.root / "existing.wav"
        output.write_bytes(b"keep existing audio")
        self.invoke(plugin, "-i", self.source, "-o", output, success=False)
        self.assertEqual(output.read_bytes(), b"keep existing audio")
        for index, assignment in enumerate(["unknown=0.5", "bypass=NaN", "bypass=2"]):
            absent = self.root / f"invalid-{index}.wav"
            self.invoke(plugin, "-i", self.source, "-o", absent, "--param", assignment, success=False)
            self.assertFalse(absent.exists())
            self.assertFalse(absent.with_suffix(".wav.json").exists())
        self.assertFalse(list(self.root.glob(".nzrdsp-*")), "Parent must clean staging directories")

    def test_linear_phase_latency_is_compensated_without_losing_frames(self):
        plugin = next((p for p in self.plugins if "Pro-Q 4" in p), None)
        if plugin is None:
            self.skipTest("Pro-Q 4 fixture is not configured")
        impulse = np.zeros((12000, 2), dtype="float32")
        impulse[1000] = [.1, .05]
        source = self.root / "impulse.wav"
        sf.write(source, impulse, 48000, subtype="FLOAT")
        output = self.root / "linear-phase.wav"
        manifest = self.invoke(plugin, "--preset", self.presets[plugin], "-i", source, "-o", output,
                               "--tail", .05, "--param", "processing_mode=1",
                               "--param", "band_1_used=1", "--param", "band_1_gain=0.625")
        actual, rate = sf.read(output, dtype="float32", always_2d=True)
        self.assertEqual((actual.shape, rate), ((14400, 2), 48000))
        self.assertGreater(manifest["reported_latency_samples"], 0)
        self.assertTrue(np.isfinite(actual).all())
        self.assertEqual(int(np.argmax(np.abs(actual[:, 0]))), 1000)


if __name__ == "__main__":
    unittest.main()
