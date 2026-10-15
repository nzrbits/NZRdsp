"""Native end-to-end checks. Set NZRDSP_SURGE_CLI to the built executable."""
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import shutil

import numpy as np
import soundfile as sf


@unittest.skipUnless(os.environ.get("NZRDSP_SURGE_CLI"), "Native CLI not configured")
class NativeSurgeTests(unittest.TestCase):
    maxDiff = None
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.cli = os.environ["NZRDSP_SURGE_CLI"]
        self.runtime_path = Path(os.environ["NZRDSP_RUNTIME"])
        self.runtime = json.loads(self.runtime_path.read_text(encoding="utf-8-sig"))
        self.patch = self.root / "init.fxp"
        self.invoke("--init-preset", self.patch)

    def invoke(self, *arguments, success=True):
        result = subprocess.run([self.cli, "--runtime", str(self.runtime_path), *map(str, arguments)],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return json.loads(result.stdout) if success else result.stderr

    def test_native_roundtrip_rates_gain_and_tail(self):
        patch_hash = hashlib.sha256(self.patch.read_bytes()).hexdigest()
        params = self.invoke("--preset", self.patch, "--list-params")["parameters"]
        self.assertEqual(params["volume"]["min"], -48)
        for rate in [44100, 48000]:
            output = self.root / f"render-{rate}.wav"
            manifest = self.invoke("--preset", self.patch, "--duration", .101,
                                   "--tail", .037, "--rate", rate, "--param", "volume=-6", "-o", output)
            audio, actual_rate = sf.read(output, dtype="float32", always_2d=True)
            self.assertEqual(actual_rate, rate)
            self.assertEqual(audio.shape, (math.ceil(.101 * rate / 32) * 32 + round(.037 * rate), 2))
            self.assertTrue(np.isfinite(audio).all())
            self.assertGreater(float(np.max(np.abs(audio))), .01)
            self.assertEqual(manifest["preset_sha256"], patch_hash)
            self.assertEqual(manifest["wav_sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(json.loads(output.with_suffix(".wav.json").read_text()), manifest)
            data_dir = self.runtime["engines"]["surge"].get("data_dir")
            if data_dir:
                self.assertEqual(Path(manifest["factory_data_path"]).resolve(), Path(data_dir).resolve())
        self.assertEqual(hashlib.sha256(self.patch.read_bytes()).hexdigest(), patch_hash)

    def test_failures_preserve_existing_outputs(self):
        output = self.root / "keep.wav"
        output.write_bytes(b"keep original")
        self.invoke("--preset", self.patch, "-o", output, success=False)
        self.assertEqual(output.read_bytes(), b"keep original")
        for assignment in ["unknown=1", "volume=99", "volume=nan", "scene.0.osc.0.type=0.5"]:
            new_path = self.root / "absent.wav"
            self.invoke("--preset", self.patch, "--param", assignment, "-o", new_path, success=False)
            self.assertFalse(new_path.exists())
            self.assertFalse(new_path.with_suffix(".wav.json").exists())
        bad = self.root / "bad.fxp"
        bad.write_bytes(b"invalid patch")
        self.invoke("--preset", bad, "-o", self.root / "bad.wav", success=False)

    def test_module_hash_mismatch_fails_before_output(self):
        self.runtime["engines"]["surge"]["module_sha256"] = "0" * 64
        self.runtime_path = self.root / "bad-runtime.json"
        self.runtime_path.write_text(json.dumps(self.runtime))
        output = self.root / "absent.wav"
        error = self.invoke("-o", output, success=False)
        self.assertIn("hash", error)
        self.assertFalse(output.exists())

    def test_metadata_catalog_search_and_corrupt_patch_reporting(self):
        before = self.patch.read_bytes()
        metadata = self.invoke("--preset", self.patch, "--metadata")
        factory_loaded = bool(self.runtime["engines"]["surge"].get("data_dir"))
        self.assertEqual(metadata["name"], "Init Saw" if factory_loaded else "Init")
        self.assertEqual(metadata["category"], "Templates" if factory_loaded else "Init")
        self.assertEqual(metadata["author"], "Surge Synth Team")
        self.assertEqual(metadata["sha256"], hashlib.sha256(before).hexdigest())
        nested = self.root / "Bässe"
        nested.mkdir()
        shutil.copyfile(self.patch, nested / "Unicode.FXP")
        (self.root / "bad.fxp").write_bytes(b"invalid")
        catalog = self.invoke("--list-presets", self.root)
        self.assertEqual((catalog["scanned"], catalog["matched"], len(catalog["errors"])), (3, 2, 1))
        filtered = self.invoke("--list-presets", self.root, "--query", "BÄSSE")
        self.assertEqual(filtered["matched"], 1)
        self.assertEqual(filtered["presets"][0]["filename"], "Unicode.FXP")
        self.assertEqual(self.patch.read_bytes(), before)

    def test_cli_matches_direct_native_block_processing(self):
        config = self.runtime["engines"]["surge"]
        sys.path.insert(0, config["module_dir"])
        if config.get("data_dir"):
            os.environ["SURGE_DATA_HOME"] = config["data_dir"]
        import surgepy
        output = self.root / "reference.wav"
        self.invoke("--preset", self.patch, "--note", 48, "--duration", .1, "--tail", 0,
                    "--param", "scene.0.osc.0.retrigger=true", "-o", output)
        engine = surgepy.createSurge(48000)
        self.assertTrue(engine.loadPatch(str(self.patch)))
        engine.setParamVal(engine.getPatch()["scene"][0]["osc"][0]["retrigger"], 1)
        engine.playNote(0, 48, 100, 0)
        pieces = []
        for _ in range(150):
            block = engine.createMultiBlock(1)
            engine.processMultiBlock(block)
            pieces.append(np.asarray(block).T.copy())
        expected = np.concatenate(pieces)
        actual, _ = sf.read(output, dtype="float32", always_2d=True)
        np.testing.assert_allclose(actual, expected, atol=1e-6, rtol=0)


if __name__ == "__main__":
    unittest.main()
