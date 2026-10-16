"""Independent differential-envelope experiment; no vendor DSP or parity claim."""
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from importlib.metadata import version

ALGORITHM = "nzrdsp-differential-envelope-v1"
TIMES_MS = ((0.2, 30.0), (10.0, 30.0), (1.0, 60.0), (1.0, 240.0))


def digest(path):
    result = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(65536), b""):
            result.update(block)
    return result.hexdigest()


def control(value, low, high, name):
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be finite in {low}..{high}")
    return value


class Detector:
    def __init__(self, rate, attack_db, sustain_db, output_db):
        self.gains = (control(attack_db, -15, 15, "Attack"), control(sustain_db, -24, 24, "Sustain"), control(output_db, -60, 24, "Output"))
        self.coefficients = tuple(tuple(math.exp(-1.0 / (rate * ms * 0.001)) for ms in times) for times in TIMES_MS)
        self.envelopes = [0.0] * 4

    def process(self, audio):
        import numpy as np
        if not np.isfinite(audio).all():
            raise ValueError("Input contains non-finite samples")
        result = np.array(audio, dtype=np.float32, copy=True)
        attack_db, sustain_db, output_db = self.gains
        for frame, samples in enumerate(audio):
            peak = max(abs(float(value)) for value in samples)
            for index, (attack, release) in enumerate(self.coefficients):
                previous = self.envelopes[index]
                coefficient = attack if peak > previous else release
                value = coefficient * previous + (1.0-coefficient) * peak
                self.envelopes[index] = value if value >= 1e-30 else 0.0
            fast, slow, short, long = self.envelopes
            attack_weight = max(0.0, fast-slow) / max(fast, 1e-30)
            sustain_weight = max(0.0, long-short) / max(long, 1e-30)
            gain = 10.0 ** ((output_db + attack_db*attack_weight + sustain_db*sustain_weight)/20.0)
            result[frame] = samples.astype(np.float64) * gain
        if not np.isfinite(result).all():
            raise ValueError("Processing exceeds finite Float32 output")
        return result


def publish(source, destination):
    if os.name == "nt":
        os.rename(source, destination)
    else:
        os.link(source, destination)


def execute(request):
    import numpy as np
    import soundfile as sf
    if version("numpy") != "2.5.2" or version("soundfile") != "0.14.0":
        raise ValueError("Use the pinned requirements-surge.txt runtime")
    source = Path(request["input"]).resolve(strict=True)
    output = Path(request["output"]).resolve()
    sidecar = output.with_suffix(output.suffix+".json")
    if output.exists() or sidecar.exists():
        raise ValueError("Choose a new output path; existing files are preserved")
    block = int(request["block"])
    if not 1 <= block <= 8192:
        raise ValueError("Block must be in 1..8192")
    source_hash = digest(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with sf.SoundFile(source) as reader:
        rate, channels, frames = reader.samplerate, reader.channels, len(reader)
        if not 8000 <= rate <= 192000 or channels not in (1,2) or not 0 < frames <= rate*600:
            raise ValueError("Use nonempty mono/stereo audio, 8..192 kHz, at most 600 seconds")
        shaper = Detector(rate, request["attack_db"], request["sustain_db"], request["output_db"])
        with tempfile.TemporaryDirectory(dir=request.get("staging_dir", output.parent)) as directory:
            temporary = Path(directory)/"audio.wav"
            peak, energy, above, written = 0.0, 0.0, 0, 0
            with sf.SoundFile(temporary,"w",rate,channels,format="WAV",subtype="FLOAT") as writer:
                while reader.tell() < frames:
                    result = shaper.process(reader.read(block,dtype="float32",always_2d=True))
                    writer.write(result)
                    peak = max(peak,float(np.max(np.abs(result))))
                    energy += float(np.sum(result.astype(np.float64)**2))
                    above += int(np.count_nonzero(np.abs(result)>1.0))
                    written += len(result)
            if written != frames or digest(source) != source_hash:
                raise ValueError("Input changed during processing")
            metadata = {"algorithm":ALGORITHM,"vendor_parity":False,"worker_sha256":digest(__file__),
                        "input":str(source),"input_sha256":source_hash,"input_subtype":reader.subtype,
                        "output":str(output),"sample_rate":rate,"channels":channels,"frames":written,
                        "block_size":block,"latency_samples":0,"attack_db":shaper.gains[0],
                        "sustain_db":shaper.gains[1],"output_db":shaper.gains[2],"envelope_times_ms":TIMES_MS,
                        "numpy":version("numpy"),"soundfile":version("soundfile"),"libsndfile":sf.__libsndfile_version__,
                        "peak":peak,"rms":math.sqrt(energy/(written*channels)),"samples_above_unity":above,
                        "wav_sha256":digest(temporary),"limitations":["Independent experiment, not SPL Transient Designer", "Fixed linked envelopes; no limiter, sidechain or vendor preset import", "Float32 output preserves over-unity samples without clipping"]}
            manifest = Path(directory)/"manifest.json"
            manifest.write_text(json.dumps(metadata,indent=2)+"\n",encoding="utf-8")
            publish(manifest,sidecar)
            try:
                publish(temporary,output)
            except Exception:
                sidecar.unlink()
                raise
    return metadata


if __name__ == "__main__":
    try:
        print(json.dumps(execute(json.load(sys.stdin))))
    except Exception as error:
        print(json.dumps({"error":str(error)}))
        sys.exit(1)
