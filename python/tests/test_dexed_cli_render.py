"""Native CLI/manifest checks with generated patches and silent offline processing."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import soundfile as sf
from .test_dexed_native import voice
from .test_dexed_sysex import bank

CLI = os.environ.get("NZRDSP_NATIVE_CLI")
RUNTIME = os.environ.get("NZRDSP_RUNTIME")


@unittest.skipUnless(CLI and RUNTIME, "set NZRDSP_NATIVE_CLI and NZRDSP_RUNTIME")
class DexedCliRender(unittest.TestCase):
    def call(self, *args, success=True):
        result = subprocess.run([CLI, "dexed", *map(str, args)], capture_output=True, text=True, encoding="utf-8", timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode == 0, success, result.stderr)
        return json.loads(result.stdout) if success else result

    def test_all_engines_match_direct_backend_and_manifest_metrics(self):
        native = json.loads(Path(RUNTIME).read_text(encoding="utf-8-sig"))["engines"]["dexed"]["executable"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Unicode \u00fc"
            root.mkdir()
            source = root / "input.syx"
            source.write_bytes(voice())
            for engine, engine_id in [("modern", 0), ("mark1", 1), ("opl", 2)]:
                for rate in [44100, 48000]:
                    output = root / f"{engine}-{rate}.wav"
                    result = self.call("--sysex", source, "--duration", ".03", "--tail", ".02", "--rate", rate, "--note", 60, "--velocity", 107, "--block", 127, "--seed", 912, "--engine", engine, "-o", output)
                    stored = json.loads(Path(str(output) + ".json").read_text())
                    self.assertEqual(result, stored)
                    self.assertEqual(result["input_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
                    self.assertEqual(result["output_sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
                    self.assertEqual(result["executable_sha256"], hashlib.sha256(Path(native).read_bytes()).hexdigest())
                    audio, actual_rate = sf.read(output, always_2d=True, dtype="float32")
                    self.assertEqual((len(audio), actual_rate, audio.shape[1]), (round(rate*.03) + round(rate*.02), rate, 2))
                    self.assertAlmostEqual(result["peak"], float(abs(audio).max()), places=14)
                    self.assertAlmostEqual(result["rms"], float(np.sqrt(np.mean(audio.astype(np.float64)**2))), places=12)
                    direct = root / f"direct-{engine}-{rate}.wav"
                    subprocess.run([native, str(source), str(direct), "60", "107", str(round(rate*.03)), str(round(rate*.02)), str(rate), "127", str(engine_id), "912"], check=True, capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    self.assertEqual(output.read_bytes(), direct.read_bytes())
            self.assertFalse(any(p.name.startswith(".nzrdsp-") for p in root.iterdir()))

    def test_dexed_cli_alias_exports_the_same_audio(self):
        alias = Path(CLI).with_name("dexed-cli.exe" if os.name == "nt" else "dexed-cli")
        self.assertTrue(alias.is_file())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source=root/"voice.syx"
            source.write_bytes(voice())
            output=root/"alias.wav"
            result=subprocess.run([str(alias),"--sysex",str(source),"--duration",".03","--tail",".02","-o",str(output)],capture_output=True,text=True,encoding="utf-8",timeout=30,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            self.assertEqual(result.returncode,0,result.stderr)
            metadata=json.loads(result.stdout)
            primary=root/"primary.wav"
            self.call("--sysex",source,"--duration",".03","--tail",".02","-o",primary)
            self.assertEqual(primary.read_bytes(),output.read_bytes())
            self.assertEqual(metadata["output_sha256"],hashlib.sha256(output.read_bytes()).hexdigest())

    def test_selected_bank_voice_and_edits_reach_native_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bank.syx"
            original = bank()
            source.write_bytes(original)
            edited = root / "edited.syx"
            edits = ["--voice", 31, "--name", "NEURO", "--param", "op1.output_level=99", "--param", "algorithm=31"]
            self.call("--sysex", source, *edits, "--save-sysex", edited)
            a = root / "bank.wav"
            b = root / "single.wav"
            first = self.call("--sysex", source, *edits, "--duration", ".1", "--tail", "0", "-o", a)
            second = self.call("--sysex", edited, "--duration", ".1", "--tail", "0", "-o", b)
            self.assertEqual(a.read_bytes(), b.read_bytes())
            self.assertEqual(first["program_bytes"], second["program_bytes"])
            self.assertEqual(first["name"], "NEURO")
            self.assertEqual(first["voice"], 31)
            self.assertEqual(source.read_bytes(), original)

    def test_invalid_requests_and_runtime_preserve_outputs_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "voice.syx"
            source.write_bytes(voice())
            output = root / "keep.wav"
            output.write_bytes(b"existing audio")
            self.call("--sysex", source, "-o", output, success=False)
            self.assertEqual(output.read_bytes(), b"existing audio")
            self.assertFalse(Path(str(output) + ".json").exists())
            target = root / "new.wav"
            sidecar = Path(str(target) + ".json")
            sidecar.write_bytes(b"existing manifest")
            self.call("--sysex", source, "-o", target, success=False)
            self.assertFalse(target.exists())
            self.assertEqual(sidecar.read_bytes(), b"existing manifest")
            for option, value in [("--duration", "NaN"), ("--duration", "0"), ("--duration", "601"), ("--tail", "-1"), ("--rate", "8000"), ("--block", "0"), ("--note", "128")]:
                self.call("--sysex", source, "-o", root / "never.wav", option, value, success=False)
            self.call("--sysex", source, "--note", "60", success=False)
            self.call("--sysex", source, "--save-sysex", root / "never.syx", "-o", root / "never.wav", success=False)
            runtime = json.loads(Path(RUNTIME).read_text(encoding="utf-8-sig"))
            bad_config = root / "bad-runtime.json"
            for field in ["source_commit", "executable_sha256"]:
                bad = json.loads(json.dumps(runtime))
                bad["engines"]["dexed"][field] = "0" * 64
                bad_config.write_text(json.dumps(bad))
                self.call("--runtime", bad_config, "--sysex", source, "-o", root / "never.wav", success=False)
            bad = json.loads(json.dumps(runtime))
            bad["engines"]["dexed"]["executable"] = sys.executable
            bad["engines"]["dexed"]["executable_sha256"] = hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
            bad_config.write_text(json.dumps(bad))
            self.call("--runtime", bad_config, "--sysex", source, "-o", root / "never.wav", success=False)
            self.assertFalse((root / "never.wav").exists())
            self.assertFalse((root / "never.syx").exists())
            self.assertFalse(any(p.name.startswith(".nzrdsp-") for p in root.iterdir()))
            self.assertEqual(source.read_bytes(), voice())


if __name__ == "__main__":
    unittest.main()
