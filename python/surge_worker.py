import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile

COMMIT = "d8b4a7bf97b39d0558830cd4799642247349bea2"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def native_parameters(engine):
    result = {}

    def visit(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, path + [str(key)])
        elif isinstance(value, (list, tuple)):
            for key, item in enumerate(value):
                visit(item, path + [str(key)])
        elif hasattr(value, "getId"):
            result[".".join(path)] = value

    visit(engine.getPatch(), [])
    return result


def descriptor(engine, parameter):
    return {"name": parameter.getName(), "id": parameter.getId().getSynthSideId(),
            "value": engine.getParamVal(parameter), "min": engine.getParamMin(parameter),
            "max": engine.getParamMax(parameter), "default": engine.getParamDef(parameter),
            "type": engine.getParamValType(parameter), "display": engine.getParamDisplay(parameter)}


def new_output(value):
    path = Path(value).resolve()
    if path.exists() or path.with_suffix(path.suffix + ".json").exists():
        raise ValueError("Choose a new output path; existing files are preserved")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def publish(temporary, destination):
    if os.name == "nt":
        os.rename(temporary, destination)
    else:
        os.link(temporary, destination)


def execute(request):
    config = request["engine"]
    if config["source_commit"] != COMMIT:
        raise ValueError("Unsupported native source commit; rebuild the pinned Surge engine")
    module_dir = Path(config["module_dir"]).resolve(strict=True)
    sys.path.insert(0, str(module_dir))
    import numpy as np
    import soundfile as sf
    import surgepy

    rate = int(request["rate"])
    if not 8000 <= rate <= 192000:
        raise ValueError("Sample rate must be between 8000 and 192000")
    module_path = Path(surgepy.__file__).resolve()
    if module_path.parent != module_dir or digest(module_path) != config["module_sha256"]:
        raise ValueError("Native module hash differs from the configured build")
    if COMMIT[:8] not in surgepy.getVersion():
        raise ValueError("Native engine version does not match the pinned source commit")
    if config.get("data_dir"):
        os.environ["SURGE_DATA_HOME"] = str(Path(config["data_dir"]).resolve(strict=True))
    engine = surgepy.createSurge(rate)
    if engine.getSampleRate() != rate:
        raise ValueError("Native sample rate differs from request")
    patch = Path(request["preset"]).resolve(strict=True) if request.get("preset") else None
    patch_hash = digest(patch) if patch else None
    if patch and not engine.loadPatch(str(patch)):
        raise ValueError("The native engine rejected the preset")
    parameters = native_parameters(engine)
    changes = {}
    for assignment in request.get("parameters", []):
        if "=" not in assignment:
            raise ValueError("Use --param path=value")
        key, raw = assignment.split("=", 1)
        if key not in parameters:
            raise ValueError(f"Unknown parameter {key}; use --list-params")
        value = {"true": 1.0, "false": 0.0}.get(raw.lower())
        value = float(raw) if value is None else value
        info = descriptor(engine, parameters[key])
        if not math.isfinite(value) or not info["min"] <= value <= info["max"]:
            raise ValueError(f"{key} must be finite between {info['min']} and {info['max']}")
        if info["type"] != "float" and not value.is_integer():
            raise ValueError(f"{key} requires an integer")
        engine.setParamVal(parameters[key], value)
        changes[key] = descriptor(engine, parameters[key])
    if request["action"] == "parameters":
        return {"engine": "Surge XT", "version": surgepy.getVersion(),
                "parameters": {key: descriptor(engine, value) for key, value in parameters.items()}}
    output = new_output(request["output"])
    if patch and output == patch:
        raise ValueError("Output must differ from the input patch")
    if request["action"] == "init":
        with tempfile.TemporaryDirectory(dir=output.parent) as directory:
            temporary = Path(directory) / "init.fxp"
            engine.savePatch(str(temporary))
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise RuntimeError("The native engine did not save a patch")
            publish(temporary, output)
        return {"engine": "Surge XT", "preset": str(output), "sha256": digest(output)}
    if request["action"] != "render":
        raise ValueError("Unknown worker action")
    note, velocity = int(request["note"]), int(request["velocity"])
    duration, tail = float(request["duration"]), float(request["tail"])
    if not 0 <= note <= 127 or not 1 <= velocity <= 127:
        raise ValueError("MIDI note or velocity is out of range")
    if not math.isfinite(duration) or not 0 < duration <= 600 or not math.isfinite(tail) or not 0 <= tail <= 60:
        raise ValueError("Invalid duration or tail")
    block = engine.getBlockSize()
    on_blocks = math.ceil(duration * rate / block)
    tail_frames = round(tail * rate)
    peak, power, frames = 0.0, 0.0, 0
    with tempfile.TemporaryDirectory(dir=output.parent) as directory:
        temporary = Path(directory) / "audio.wav"
        with sf.SoundFile(temporary, "w", rate, 2, format="WAV", subtype="FLOAT") as writer:
            engine.playNote(0, note, velocity, 0)
            for length, release in [(on_blocks * block, False), (tail_frames, True)]:
                if release:
                    engine.releaseNote(0, note, 0)
                remaining = length
                while remaining:
                    count = min(256, math.ceil(remaining / block))
                    native = engine.createMultiBlock(count)
                    engine.processMultiBlock(native)
                    audio = np.asarray(native).T[:remaining]
                    if audio.ndim != 2 or audio.shape[1] != 2 or not np.isfinite(audio).all():
                        raise RuntimeError("Native output has invalid samples or shape")
                    peak = max(peak, float(np.max(np.abs(audio))))
                    power += float(np.sum(audio.astype(np.float64) ** 2))
                    writer.write(audio)
                    frames += len(audio)
                    remaining -= len(audio)
        if patch and digest(patch) != patch_hash:
            raise RuntimeError("Input patch changed during rendering")
        info = sf.info(temporary)
        if (info.frames, info.channels, info.samplerate) != (frames, 2, rate):
            raise RuntimeError("Written WAV differs from native output")
        manifest = {"engine": "Surge XT", "version": surgepy.getVersion(), "source_commit": COMMIT,
                    "module_sha256": config["module_sha256"], "preset": str(patch) if patch else None,
                    "factory_data_path": engine.getFactoryDataPath(), "user_data_path": engine.getUserDataPath(),
                    "preset_sha256": patch_hash, "parameters": changes,
                    "sample_rate": rate, "block_size": block, "note": note, "velocity": velocity,
                    "requested_note_seconds": duration, "actual_note_frames": on_blocks * block,
                    "tail_frames": tail_frames, "frames": frames, "peak": peak,
                    "rms": math.sqrt(power / (frames * 2)), "wav_sha256": digest(temporary),
                    "limitations": ["Note-off rounds up to a native 32-sample block",
                                    "Tempo follows native default or preset; no BPM override",
                                    "Random seed control is not exposed"]}
        manifest_tmp = Path(directory) / "manifest.json"
        manifest_tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        publish(temporary, output)
        try:
            publish(manifest_tmp, output.with_suffix(output.suffix + ".json"))
        except Exception:
            output.unlink()
            raise
    return manifest


if __name__ == "__main__":
    try:
        result = execute(json.load(sys.stdin))
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        sys.exit(1)
    print(json.dumps(result))
