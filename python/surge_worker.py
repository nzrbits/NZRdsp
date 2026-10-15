import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import struct
import xml.etree.ElementTree as ET

COMMIT = "d8b4a7bf97b39d0558830cd4799642247349bea2"


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as reader:
        for block in iter(lambda: reader.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def patch_metadata(path):
    path = Path(path).resolve(strict=True)
    with path.open("rb") as reader:
        header = reader.read(60)
        if len(header) != 60 or header[:4] != b"CcnK" or header[8:12] != b"FPCh" or header[16:20] != b"cjs3":
            raise ValueError("Not a native Surge FXP patch")
        chunk_size = struct.unpack(">I", header[56:60])[0]
        if chunk_size == 0 or chunk_size > os.fstat(reader.fileno()).st_size - 60:
            raise ValueError("Truncated or empty native patch chunk")
        prefix = reader.read(min(32, chunk_size))
        if prefix[:4] == b"sub3":
            if len(prefix) != 32:
                raise ValueError("Truncated native patch header")
            xml_size, *wave_sizes = struct.unpack("<7I", prefix[4:])
            if xml_size == 0 or 32 + xml_size + sum(wave_sizes) > chunk_size:
                raise ValueError("Invalid XML or wavetable sizes in native patch")
            if xml_size > 16 * 1024 * 1024:
                raise ValueError("Patch metadata exceeds 16 MiB")
            xml = reader.read(xml_size)
            if len(xml) != xml_size:
                raise ValueError("Truncated native patch XML")
            format_name = "surge-sub3"
        else:
            if chunk_size > 16 * 1024 * 1024:
                raise ValueError("Legacy patch XML exceeds 16 MiB")
            reader.seek(60)
            xml = reader.read(chunk_size)
            format_name = "surge-xml"
    upper_xml = xml.upper()
    if b"<!DOCTYPE" in upper_xml or b"<!ENTITY" in upper_xml:
        raise ValueError("Patch metadata must not declare DTDs or entities")
    root, meta = patch_meta_element(xml.rstrip(b"\0"))
    result = {key: meta.get(key, "") if meta is not None else "" for key in ["name", "category", "author", "comment", "license"]}
    result["name"] = result["name"] or header[28:56].split(b"\0", 1)[0].decode("utf-8", errors="replace") or path.stem
    result.update(path=str(path), filename=path.name, format=format_name,
                  revision=root.get("revision"), sha256=digest(path),
                  tags=[tag.get("tag", "") for tag in meta.findall("tags/tag") if tag.get("tag")] if meta is not None else [])
    return result


def patch_meta_element(xml):
    parser = ET.XMLPullParser(events=["start", "end"])
    root, depth = {}, 0
    for offset in range(0, len(xml), 1024):
        parser.feed(xml[offset:offset + 1024])
        for event, element in parser.read_events():
            if event == "start":
                if depth == 0:
                    if element.tag != "patch":
                        raise ValueError("Native patch XML has no patch root")
                    root = dict(element.attrib)
                depth += 1
            else:
                if depth == 2 and element.tag == "meta":
                    return root, element
                depth -= 1
    parser.close()
    return root, None


def patch_catalog(folder, query=None):
    root = Path(folder).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Preset catalog root must be a directory")
    presets, errors, scanned = [], [], 0
    def unreadable(error):
        errors.append({"path": str(error.filename), "error": str(error)})

    for directory, folders, files in os.walk(root, followlinks=False, onerror=unreadable):
        folders[:] = sorted(name for name in folders if not (Path(directory) / name).is_symlink()
                            and not (Path(directory) / name).is_junction())
        for filename in sorted(files):
            path = Path(directory) / filename
            if path.suffix.lower() != ".fxp" or path.is_symlink():
                continue
            scanned += 1
            try:
                item = patch_metadata(path)
                item["relative_path"] = str(path.relative_to(root))
                text = " ".join(str(item[key]) for key in ["name", "category", "author", "comment", "tags", "relative_path"])
                if not query or query.casefold() in text.casefold():
                    presets.append(item)
            except (OSError, ValueError, ET.ParseError) as error:
                errors.append({"path": str(path), "error": str(error)})
    presets.sort(key=lambda item: (item["category"].casefold(), item["name"].casefold(), item["path"].casefold()))
    return {"root": str(root), "query": query, "scanned": scanned, "matched": len(presets), "presets": presets, "errors": errors}


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
    if request["action"] == "metadata":
        return patch_metadata(request["preset"])
    if request["action"] == "catalog":
        return patch_catalog(request["root"], request.get("query"))
    config = request["engine"]
    if config["source_commit"] != COMMIT:
        raise ValueError("Unsupported native source commit; rebuild the pinned Surge engine")
    module_dir = Path(config["module_dir"]).resolve(strict=True)
    sys.path.insert(0, str(module_dir))
    if config.get("data_dir"):
        os.environ["SURGE_DATA_HOME"] = str(Path(config["data_dir"]).resolve(strict=True))
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
    engine = surgepy.createSurge(rate)
    if engine.getSampleRate() != rate:
        raise ValueError("Native sample rate differs from request")
    patch = Path(request["preset"]).resolve(strict=True) if request.get("preset") else None
    patch_hash = digest(patch) if patch else None
    patch_info = patch_metadata(patch) if patch else None
    if patch_info and patch_info["sha256"] != patch_hash:
        raise RuntimeError("Input patch changed while reading metadata")
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
        with tempfile.TemporaryDirectory(dir=request.get("staging_dir", output.parent)) as directory:
            temporary = Path(directory) / "init.fxp"
            engine.savePatch(str(temporary))
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise RuntimeError("The native engine did not save a patch")
            publish(temporary, output)
        return {"engine": "Surge XT", "preset": str(output), "sha256": digest(output), "metadata": patch_metadata(output)}
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
    with tempfile.TemporaryDirectory(dir=request.get("staging_dir", output.parent)) as directory:
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
                    "preset_metadata": patch_info,
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
