import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import sys
import tempfile


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as reader:
        for block in iter(lambda: reader.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def new_output(value):
    path = Path(value).resolve()
    if path.exists() or path.with_suffix(path.suffix + ".json").exists():
        raise ValueError("Choose a new output path; existing files are preserved")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def publish(source, destination):
    if os.name == "nt":
        os.rename(source, destination)
    else:
        os.link(source, destination)


def module_hashes(path):
    if path.is_file():
        return {path.name: digest(path)}
    binaries = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in [".vst3", ".dll"])
    if not binaries:
        raise ValueError("No native module found in the VST3 bundle")
    return {str(p.relative_to(path)): digest(p) for p in binaries}


def execute(request):
    if version("pedalboard") != "0.9.26":
        raise ValueError("Install the pinned pedalboard 0.9.26 runtime")
    import numpy as np
    import soundfile as sf
    from pedalboard import load_plugin

    path = Path(request["plugin"]).resolve(strict=True)
    if path.suffix.lower() != ".vst3":
        raise ValueError("This command accepts native VST3 modules only")
    output = new_output(request["output"]) if request.get("output") else None
    hashes = module_hashes(path)
    plugin = load_plugin(str(path), plugin_name=request.get("name"), initialization_timeout=10)
    preset = Path(request["preset"]).resolve(strict=True) if request.get("preset") else None
    preset_hash = digest(preset) if preset else None
    if preset:
        if preset.suffix.lower() != ".vstpreset" or preset.read_bytes()[:4] != b"VST3":
            raise ValueError("Use a VST3 .vstpreset, not a vendor-specific preset container")
        plugin.load_preset(str(preset))
    parameters = plugin.parameters
    changes = {}
    for assignment in request["parameters"]:
        if "=" not in assignment:
            raise ValueError("Use --param host_key=normalized_value")
        key, raw = assignment.split("=", 1)
        if key not in parameters:
            raise ValueError(f"Unknown parameter {key}; use --list-params")
        value = float(raw)
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("VST parameter values must be finite and normalized between 0 and 1")
        parameters[key].raw_value = value
        changes[key] = {"requested": value, "actual": parameters[key].raw_value, "display": str(parameters[key])}
    metadata = {"host": "pedalboard", "host_version": version("pedalboard"),
                "plugin": str(path), "module_hashes": hashes, "name": plugin.name,
                "manufacturer": plugin.manufacturer_name, "version": plugin.version,
                "identifier": plugin.identifier, "is_instrument": plugin.is_instrument,
                "preset": str(preset) if preset else None, "preset_sha256": preset_hash,
                "parameter_overrides": changes,
                "raw_parameters": {key: value.raw_value for key, value in parameters.items()}}
    if request["action"] == "parameters":
        metadata["parameters"] = {key: {"raw_value": value.raw_value, "display": str(value)} for key, value in parameters.items()}
        return metadata
    if request["action"] == "save":
        if output.suffix.lower() != ".vstpreset":
            raise ValueError("Save native host state to a .vstpreset file")
        with tempfile.TemporaryDirectory(dir=request.get("staging_dir", output.parent)) as directory:
            temporary = Path(directory) / "state.vstpreset"
            temporary.write_bytes(plugin.preset_data)
            publish(temporary, output)
        metadata.update(output=str(output), sha256=digest(output))
        return metadata
    if request["action"] != "render" or not plugin.is_effect:
        raise ValueError("Rendering currently accepts audio effects, not MIDI instruments")
    source = Path(request["input"]).resolve(strict=True)
    source_hash = digest(source)
    tail = float(request["tail"])
    block = int(request["block"])
    if not math.isfinite(tail) or not 0 <= tail <= 60 or not 1 <= block <= 8192:
        raise ValueError("Invalid tail or block size")
    initial_state_hash = hashlib.sha256(plugin.preset_data).hexdigest()
    peak, power, written, clipped = 0.0, 0.0, 0, 0
    with sf.SoundFile(source) as reader:
        rate, channels, source_frames = reader.samplerate, reader.channels, len(reader)
        if not 8000 <= rate <= 384000 or channels not in [1, 2] or source_frames == 0:
            raise ValueError("Input must contain nonempty mono/stereo audio at 8 to 384 kHz")
        requested_frames = source_frames + round(tail * rate)
        if requested_frames * channels * 4 + 512 >= 2 ** 32:
            raise ValueError("Output exceeds the WAV 4 GiB limit")
        plugin.reset()
        with tempfile.TemporaryDirectory(dir=request.get("staging_dir", output.parent)) as directory:
            temporary = Path(directory) / "audio.wav"
            with sf.SoundFile(temporary, "w", rate, channels, format="WAV", subtype="FLOAT") as writer:
                def process(audio):
                    nonlocal peak, power, written, clipped
                    if not np.isfinite(audio).all():
                        raise ValueError("Input contains non-finite samples")
                    result = np.asarray(plugin.process(np.ascontiguousarray(audio.T), rate, buffer_size=block, reset=False))
                    if result.ndim != 2 or result.shape[0] != channels or result.shape[1] > len(audio) or not np.isfinite(result).all():
                        raise RuntimeError("Native plugin returned invalid audio")
                    result = result[:, :requested_frames - written].T
                    if len(result):
                        peak = max(peak, float(np.max(np.abs(result))))
                        power += float(np.sum(result.astype(np.float64) ** 2))
                        clipped += int(np.sum(np.abs(result) > 1.0))
                        writer.write(result)
                        written += len(result)

                while reader.tell() < source_frames:
                    process(reader.read(8192, dtype="float32", always_2d=True))
                remaining_tail = requested_frames - source_frames
                while remaining_tail:
                    count = min(8192, remaining_tail)
                    process(np.zeros((count, channels), dtype=np.float32))
                    remaining_tail -= count
                flush_budget = int(plugin.reported_latency_samples) + 2 * block
                while written < requested_frames and flush_budget:
                    count = min(8192, flush_budget)
                    process(np.zeros((count, channels), dtype=np.float32))
                    flush_budget -= count
                if written != requested_frames:
                    raise RuntimeError("Host did not return the expected latency-compensated frame count")
            if digest(source) != source_hash or (preset and digest(preset) != preset_hash):
                raise RuntimeError("Input or preset changed during processing")
            metadata.update(input=str(source), input_sha256=source_hash, initial_state_sha256=initial_state_hash,
                            sample_rate=rate, channels=channels, block_size=block, source_frames=source_frames,
                            tail_frames=requested_frames - source_frames, frames=written,
                            reported_latency_samples=plugin.reported_latency_samples,
                            peak=peak, rms=math.sqrt(power / (written * channels)), samples_above_unity=clipped,
                            wav_sha256=digest(temporary), limitations=["Native installed plugin required",
                            "No explicit BPM/transport override", "Host preset format only; no vendor container parser",
                            "Randomized native behavior may prevent exact repeatability"])
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            publish(temporary, output)
            try:
                publish(manifest, output.with_suffix(output.suffix + ".json"))
            except Exception:
                output.unlink()
                raise
    return metadata


if __name__ == "__main__":
    try:
        print(json.dumps(execute(json.load(sys.stdin))))
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        sys.exit(1)
