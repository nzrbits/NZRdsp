"""Native spectral CLI integration; generated fixtures, no playback or GUI."""
import hashlib
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import numpy as np
import soundfile as sf

CLI = os.environ.get("NZRDSP_NATIVE_CLI")
RUNTIME = os.environ.get("NZRDSP_RUNTIME")
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@unittest.skipUnless(CLI and RUNTIME, "set NZRDSP_NATIVE_CLI and NZRDSP_RUNTIME")
class VitalCli(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vital-cli-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "Unicode ä"
        self.root.mkdir()
        self.runtime = json.loads(Path(RUNTIME).read_text(encoding="utf-8-sig"))
        self.native = self.runtime["engines"]["vital"]["executable"]

    def call(self, *args, success=True, alias=False):
        executable = Path(CLI).with_name("vital-cli.exe" if os.name == "nt" else "vital-cli") if alias else Path(CLI)
        command = [str(executable)] + ([] if alias else ["vital"]) + list(map(str, args))
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=30, creationflags=FLAGS)
        self.assertEqual(result.returncode == 0, success, result.stderr)
        return json.loads(result.stdout) if success else result

    def assert_manifest(self, output, result):
        stored = json.loads(Path(str(output) + ".json").read_text(encoding="utf-8"))
        self.assertEqual(stored, result)
        self.assertEqual(result["output_sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual(result["executable_sha256"], hashlib.sha256(Path(self.native).read_bytes()).hexdigest())
        self.assertEqual(result["source_commit"], "636ca0ef517a4db087a6a08a6a8a5e704e21f836")
        self.assertFalse(any(p.name.startswith(".nzrdsp-") for p in self.root.iterdir()))

    def test_render_matches_direct_backend_at_multiple_rates_and_records_metrics(self):
        for rate in (8000, 44100, 48000, 192000):
            with self.subTest(rate=rate):
                output = self.root / f"cli-{rate}.wav"
                result = self.call("--rate", rate, "--duration", ".030021", "--tail", ".020019",
                    "--note", 60, "--velocity", 107, "--bpm", 176.25, "--seed", 17,
                    "--param", "osc_1_transpose=-12", "-o", output)
                self.assert_manifest(output, result)
                audio, actual_rate = sf.read(output, dtype="float32", always_2d=True)
                frames = round(rate * .030021) + round(rate * .020019)
                self.assertEqual((actual_rate, audio.shape), (rate, (frames, 2)))
                self.assertTrue(np.isfinite(audio).all())
                self.assertAlmostEqual(result["measured"]["peak"], float(abs(audio).max()), places=14)
                self.assertAlmostEqual(result["measured"]["rms"], float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))), places=12)
                self.assertEqual(result["measured"]["samples_above_unity"], int(np.sum(abs(audio) > 1)))
                self.assertEqual(result["preset_parameter_values"]["osc_1_transpose"], -12)
                direct = self.root / f"direct-{rate}.wav"
                request = self.root / "native-request.json"
                request.write_text(json.dumps(dict(action="render", output=str(direct), sample_rate=rate,
                    duration_frames=round(rate * .030021), tail_frames=round(rate * .020019),
                    note=60, velocity=107, bpm=176.25, seed=17, parameters={"osc_1_transpose": -12})), encoding="utf-8")
                subprocess.run([self.native, str(request)], check=True, capture_output=True, timeout=30, creationflags=FLAGS)
                self.assertEqual(output.read_bytes(), direct.read_bytes())

    def test_alias_native_preset_edits_and_reloading_preserve_audio_and_source(self):
        saved = self.root / "edited.vital"
        result = self.call("--save-preset", saved, "--param", "osc_1_transpose=12", "--param", "filter_1_on=1", alias=True)
        self.assert_manifest(saved, result)
        original = saved.read_bytes()
        state = json.loads(original)
        self.assertEqual(state["synth_version"], "1.0.6")
        self.assertEqual(state["settings"]["osc_1_transpose"], 12)
        listed = self.call("--preset", saved, "--list-params", "--param", "osc_1_transpose=-12")
        self.assertEqual(listed["parameter_count"], 772)
        self.assertEqual(listed["parameters"]["osc_1_transpose"]["value"], -12)
        self.assertEqual(listed["input_sha256"], hashlib.sha256(original).hexdigest())
        output = self.root / "alias.wav"
        first = self.call("--preset", saved, "--duration", ".1", "--tail", ".1", "--seed", 9, "-o", output, alias=True)
        other = self.root / "primary.wav"
        second = self.call("--preset", saved, "--duration", ".1", "--tail", ".1", "--seed", 9, "-o", other)
        self.assertEqual(output.read_bytes(), other.read_bytes())
        self.assertEqual(first["input_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(first["preset_parameter_values"], second["preset_parameter_values"])
        self.assertEqual(saved.read_bytes(), original)
        self.assert_manifest(output, first)

    def test_rejected_edits_runtime_mismatch_and_existing_paths_publish_nothing(self):
        output = self.root / "keep.wav"
        output.write_bytes(b"existing audio")
        self.call("-o", output, success=False)
        self.assertEqual(output.read_bytes(), b"existing audio")
        new = self.root / "new.wav"
        sidecar = Path(str(new) + ".json")
        sidecar.write_bytes(b"existing manifest")
        self.call("-o", new, success=False)
        self.assertFalse(new.exists())
        self.assertEqual(sidecar.read_bytes(), b"existing manifest")
        invalid = [(), ("--duration", "NaN"), ("--duration", "0"), ("--duration", "601"),
            ("--tail", "61"), ("--rate", "7999"), ("--bpm", "NaN"), ("--bpm", "901"),
            ("--param", "unknown=0"), ("--param", "osc_1_on=0.9999999999999"),
            ("--param", "osc_1_on=1", "--param", "osc_1_on=0"), ("--param", "osc_1_transpose=49"),
            ("--param", "osc_1_on=NaN"), ("--param", "osc_1_on=1e40")]
        for index, flags in enumerate(invalid):
            with self.subTest(flags=flags):
                destination = self.root / f"invalid-{index}.wav"
                if flags:
                    self.call(*flags, "-o", destination, success=False)
                else:
                    self.call(success=False)
                self.assertFalse(destination.exists())
                self.assertFalse(Path(str(destination) + ".json").exists())
        config = self.root / "invalid-runtime.json"
        for field in ("source_commit", "engine_version", "executable_sha256"):
            bad = json.loads(json.dumps(self.runtime))
            bad["engines"]["vital"][field] = "invalid"
            config.write_text(json.dumps(bad), encoding="utf-8")
            self.call("--runtime", config, "--list-params", success=False)
        self.assertFalse(any(p.name.startswith(".nzrdsp-") for p in self.root.iterdir()))

    def test_failed_native_process_cleans_staging_and_preserves_existing_preset(self):
        preset = self.root / "source.vital"
        self.call("--save-preset", preset)
        original = preset.read_bytes()
        self.call("--save-preset", preset, success=False)
        self.assertEqual(preset.read_bytes(), original)
        bad = json.loads(json.dumps(self.runtime))
        bad["engines"]["vital"]["executable"] = sys.executable
        bad["engines"]["vital"]["executable_sha256"] = hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
        config = self.root / "bad-runtime.json"
        config.write_text(json.dumps(bad), encoding="utf-8")
        for flag, name in (("-o", "never.wav"), ("--save-preset", "never.vital")):
            destination = self.root / name
            self.call("--runtime", config, "--preset", preset, flag, destination, success=False)
            self.assertFalse(destination.exists())
            self.assertFalse(Path(str(destination) + ".json").exists())
        self.assertEqual(preset.read_bytes(), original)
        self.assertFalse(any(p.name.startswith(".nzrdsp-") for p in self.root.iterdir()))

    def test_real_render_deadline_kills_worker_and_removes_staged_audio(self):
        output = self.root / "timeout.wav"
        started = time.monotonic()
        result = self.call("--duration", 600, "--rate", 192000, "--timeout", 1, "-o", output, success=False)
        self.assertIn("timeout", result.stderr.lower())
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(output.exists())
        self.assertFalse(Path(str(output) + ".json").exists())
        self.assertFalse(any(p.name.startswith(".nzrdsp-") for p in self.root.iterdir()))

    def test_embedded_resources_and_lfo_routing_survive_preset_export(self):
        initialized = self.root / "init.vital"
        self.call("--save-preset", initialized)
        state = json.loads(initialized.read_bytes())
        settings = state["settings"]
        wave = np.sin(2 * np.pi * np.arange(2048) / 2048).astype("<f4")
        settings["wavetables"][0]["groups"][0]["components"][0]["keyframes"][0]["wave_data"] = base64.b64encode(wave.tobytes()).decode()
        settings["wavetables"][0]["name"] = "Synthetic sine"
        settings["lfos"][0]["points"][2] = .25
        settings["modulations"][0].update(source="lfo_1", destination="osc_1_level")
        settings.update(modulation_1_amount=-.6, lfo_1_sync=0, lfo_1_frequency=2, osc_1_random_phase=0)
        pcm = np.round(10000 * np.sin(2 * np.pi * np.arange(512) / 32)).astype("<i2")
        settings["sample"] = dict(name="Synthetic sample", sample_rate=48000, length=len(pcm),
            samples=base64.b64encode(pcm.tobytes()).decode())
        settings.update(sample_on=1, sample_level=.25)
        source = self.root / "resources.vital"
        source.write_text(json.dumps(state), encoding="utf-8")
        exported = self.root / "resources-export.vital"
        self.call("--preset", source, "--save-preset", exported)
        restored = json.loads(exported.read_bytes())["settings"]
        self.assertEqual(restored["wavetables"], settings["wavetables"])
        self.assertEqual(restored["lfos"], settings["lfos"])
        self.assertEqual(restored["modulations"][0], settings["modulations"][0])
        self.assertEqual(restored["sample"]["name"], "Synthetic sample")
        self.assertEqual(restored["sample"]["length"], 512)
        self.assertEqual(restored["sample"]["sample_rate"], 48000)
        self.assertEqual(restored["sample"], settings["sample"])
        first = self.root / "resources.wav"
        second = self.root / "resources-export.wav"
        for preset, output in ((source, first), (exported, second)):
            self.call("--preset", preset, "--duration", .3, "--tail", .1, "--seed", 17, "-o", output)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        dry = self.root / "no-modulation.wav"
        self.call("--preset", source, "--duration", .3, "--tail", .1, "--seed", 17,
            "--param", "modulation_1_amount=0", "-o", dry)
        wet_audio = sf.read(first, dtype="float32", always_2d=True)[0]
        dry_audio = sf.read(dry, dtype="float32", always_2d=True)[0]
        self.assertGreater(float(np.max(abs(wet_audio-dry_audio))), .01)
