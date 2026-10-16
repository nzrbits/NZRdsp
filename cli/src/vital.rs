use crate::{native, worker};
use anyhow::{bail, Context, Result};
use clap::Args;
use serde_json::{json, Map, Value};
use std::{
    fs::{self, File},
    io::{Read, Write},
    path::{Path, PathBuf},
    process::Command,
    time::{Duration, Instant},
};

const PIN: &str = "636ca0ef517a4db087a6a08a6a8a5e704e21f836";
const VERSION: &str = "1.0.6";
const MAX_PRESET_BYTES: u64 = 64 * 1024 * 1024;

#[derive(Args)]
pub struct VitalArgs {
    #[arg(long)]
    runtime: Option<PathBuf>,
    /// Native .vital preset; omit for the initialized patch
    #[arg(long)]
    preset: Option<PathBuf>,
    /// List native raw controls, ranges and current values as JSON
    #[arg(long, conflicts_with_all = ["output", "save_preset"])]
    list_params: bool,
    /// Save the loaded/edited state to a new native preset and manifest
    #[arg(long, conflicts_with = "output")]
    save_preset: Option<PathBuf>,
    /// Write a new Float32 stereo WAV and .wav.json manifest
    #[arg(short, long)]
    output: Option<PathBuf>,
    #[arg(long, requires = "output", conflicts_with_all = ["list_params", "save_preset"], default_value_t = 36, value_parser = clap::value_parser!(u8).range(0..=127))]
    note: u8,
    #[arg(long, requires = "output", conflicts_with_all = ["list_params", "save_preset"], default_value_t = 100, value_parser = clap::value_parser!(u8).range(1..=127))]
    velocity: u8,
    #[arg(long, requires = "output", conflicts_with_all = ["list_params", "save_preset"], default_value_t = 2.0)]
    duration: f64,
    #[arg(long, requires = "output", conflicts_with_all = ["list_params", "save_preset"], default_value_t = 2.0)]
    tail: f64,
    #[arg(long, requires = "output", conflicts_with_all = ["list_params", "save_preset"], default_value_t = 48000, value_parser = clap::value_parser!(u32).range(8000..=192000))]
    rate: u32,
    #[arg(long, requires = "output", conflicts_with_all = ["list_params", "save_preset"], default_value_t = 120.0)]
    bpm: f64,
    #[arg(long, default_value_t = 0, value_parser = clap::value_parser!(u32).range(0..=1000000000))]
    seed: u32,
    /// Native raw assignment, e.g. osc_1_transpose=-12; see --list-params
    #[arg(long = "param")]
    parameters: Vec<String>,
    /// Shared deadline in seconds for native subprocesses
    #[arg(long, default_value_t = 120, value_parser = clap::value_parser!(u32).range(1..=3600))]
    timeout: u32,
}

fn assignments(values: &[String]) -> Result<Map<String, Value>> {
    let mut result = Map::new();
    for assignment in values {
        let (name, text) = assignment
            .split_once('=')
            .context("Use --param name=value")?;
        let value: f64 = text.parse().context("Parameter value must be numeric")?;
        if name.is_empty() || !value.is_finite() || !(value as f32).is_finite() {
            bail!("Use a nonempty parameter name and a finite Float32 value");
        }
        if result.insert(name.into(), json!(value)).is_some() {
            bail!("Duplicate parameter assignment: {name}");
        }
    }
    Ok(result)
}

fn read_preset(path: &Path) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    File::open(path)?
        .take(MAX_PRESET_BYTES + 1)
        .read_to_end(&mut bytes)?;
    if bytes.is_empty() || bytes.len() as u64 > MAX_PRESET_BYTES {
        bail!("Native presets must contain at most 64 MiB");
    }
    Ok(bytes)
}

fn validate_edits(metadata: &Value, edits: &Map<String, Value>) -> Result<Map<String, Value>> {
    let controls = metadata["parameters"]
        .as_object()
        .context("Native parameter metadata missing")?;
    if controls.len() != 772 || metadata["parameter_count"].as_u64() != Some(772) {
        bail!("Native parameter count differs from the pinned build");
    }
    let mut values = Map::new();
    for (name, control) in controls {
        let min = control["min"].as_f64().context("Native minimum missing")?;
        let max = control["max"].as_f64().context("Native maximum missing")?;
        let current = control["value"].as_f64().context("Native value missing")?;
        let indexed = control["indexed"]
            .as_bool()
            .context("Native indexed flag missing")?;
        if !min.is_finite()
            || !max.is_finite()
            || min > max
            || !current.is_finite()
            || current < min
            || current > max
            || (indexed && current.fract() != 0.0)
        {
            bail!("Invalid native parameter metadata: {name}");
        }
        let value = edits.get(name).and_then(Value::as_f64).unwrap_or(current);
        if value < min || value > max || (indexed && value.fract() != 0.0) {
            bail!("Parameter {name} is outside its native range or requires an integer");
        }
        values.insert(name.clone(), json!(value as f32));
    }
    for name in edits.keys() {
        if !controls.contains_key(name) {
            bail!("Unknown native parameter: {name}");
        }
    }
    Ok(values)
}

fn call(
    executable: &Path,
    staging: &Path,
    request: &Value,
    started: Instant,
    timeout: Duration,
) -> Result<Value> {
    let bytes = serde_json::to_vec(request)?;
    if bytes.len() > 1024 * 1024 {
        bail!("Native request exceeds 1 MiB");
    }
    let path = staging.join("request.json");
    fs::write(&path, bytes)?;
    let remaining = timeout
        .checked_sub(started.elapsed())
        .filter(|d| !d.is_zero())
        .context("Native operation exceeded its timeout")?;
    let mut command = Command::new(executable);
    command.arg(&path);
    let result = worker::execute_native(command, remaining)?;
    if result["source_commit"].as_str() != Some(PIN)
        || result["engine_version"].as_str() != Some(VERSION)
        || result["seed"] != request["seed"]
        || result["action"] != request["action"]
    {
        bail!("Native response version/request mismatch");
    }
    Ok(result)
}

pub fn run(args: VitalArgs) -> Result<()> {
    let action = if args.list_params {
        "parameters"
    } else if args.save_preset.is_some() {
        "save"
    } else if args.output.is_some() {
        "render"
    } else {
        bail!("Use --list-params, --save-preset or --output");
    };
    let edits = assignments(&args.parameters)?;
    let duration = native::frames(args.duration, args.rate, 600.0, true)?;
    let tail = native::frames(args.tail, args.rate, 60.0, false)?;
    if !args.bpm.is_finite() || !(5.0..=900.0).contains(&args.bpm) {
        bail!("BPM must be finite between 5 and 900");
    }
    let config = worker::runtime(args.runtime)?;
    let engine = &config["engines"]["vital"];
    if engine["source_commit"].as_str() != Some(PIN)
        || engine["engine_version"].as_str() != Some(VERSION)
    {
        bail!("Spectral runtime source/version mismatch");
    }
    let executable = PathBuf::from(
        engine["executable"]
            .as_str()
            .context("Configure engines.vital.executable with build-vital.ps1")?,
    )
    .canonicalize()?;
    let executable_hash = native::file_hash(&executable)?;
    if !engine["executable_sha256"]
        .as_str()
        .is_some_and(|expected| executable_hash.eq_ignore_ascii_case(expected))
    {
        bail!("Spectral executable SHA256 mismatch");
    }
    let destination = args
        .output
        .as_ref()
        .or(args.save_preset.as_ref())
        .map(|path| -> Result<PathBuf> {
            if path.is_absolute() {
                Ok(path.clone())
            } else {
                Ok(std::env::current_dir()?.join(path))
            }
        })
        .transpose()?;
    let sidecar = destination.as_ref().map(|path| {
        let mut text = path.as_os_str().to_os_string();
        text.push(".json");
        PathBuf::from(text)
    });
    if let (Some(output), Some(sidecar)) = (&destination, &sidecar) {
        if output.try_exists()? || sidecar.try_exists()? {
            bail!("Output and manifest destinations must both be new");
        }
        fs::create_dir_all(output.parent().context("Output needs a parent directory")?)?;
    }
    let staging = if let Some(output) = &destination {
        tempfile::Builder::new()
            .prefix(".nzrdsp-vital-")
            .tempdir_in(output.parent().unwrap())?
    } else {
        tempfile::tempdir()?
    };
    let staging_path = staging.path().canonicalize()?;
    let mut input_path = None;
    let mut input_hash = None;
    let mut captured = None;
    if let Some(source) = args.preset {
        let source = source.canonicalize()?;
        let bytes = read_preset(&source)?;
        let folder = staging_path.join("input");
        fs::create_dir(&folder)?;
        let copy = folder.join(source.file_name().context("Preset needs a filename")?);
        fs::write(&copy, &bytes)?;
        input_hash = Some(native::file_hash(&copy)?);
        input_path = Some(source);
        captured = Some(copy);
    }
    let started = Instant::now();
    let timeout = Duration::from_secs(u64::from(args.timeout));
    let mut request = json!({"action":"parameters","preset":captured,"seed":args.seed});
    let mut metadata = call(&executable, &staging_path, &request, started, timeout)?;
    let values = validate_edits(&metadata, &edits)?;
    request["parameters"] = json!(edits);
    if action == "parameters" {
        if !edits.is_empty() {
            metadata = call(&executable, &staging_path, &request, started, timeout)?;
        }
        for (name, value) in &values {
            if metadata["parameters"][name]["value"] != *value {
                bail!("Native parameter edit mismatch: {name}");
            }
        }
        if native::file_hash(&executable)? != executable_hash {
            bail!("Native executable changed during operation");
        }
        metadata["executable"] = json!(executable);
        metadata["executable_sha256"] = json!(executable_hash);
        metadata["input_preset"] = json!(input_path);
        metadata["input_sha256"] = json!(input_hash);
        println!("{}", serde_json::to_string_pretty(&metadata)?);
        return Ok(());
    }
    request["action"] = json!(action);
    let staged_output = staging_path.join(if action == "save" {
        "state.vital"
    } else {
        "audio.wav"
    });
    request["output"] = json!(staged_output);
    if action == "render" {
        request["note"] = json!(args.note);
        request["velocity"] = json!(args.velocity);
        request["duration_frames"] = json!(duration);
        request["tail_frames"] = json!(tail);
        request["sample_rate"] = json!(args.rate);
        request["bpm"] = json!(args.bpm as f32);
    }
    let result = call(&executable, &staging_path, &request, started, timeout)?;
    let output_hash;
    let mut measured = Value::Null;
    if action == "render" {
        let frames = duration.checked_add(tail).context("Frame count overflow")?;
        for (key, expected) in [
            ("frames", frames),
            ("sample_rate", args.rate),
            ("duration_frames", duration),
            ("tail_frames", tail),
            ("note", u32::from(args.note)),
            ("velocity", u32::from(args.velocity)),
            ("channels", 2),
            ("maximum_block_size", 64),
        ] {
            if result[key].as_u64() != Some(u64::from(expected)) {
                bail!("Native render metadata mismatch: {key}");
            }
        }
        if result["bpm"] != json!(args.bpm as f32) || result["format"].as_str() != Some("float32") {
            bail!("Native render tempo/format mismatch");
        }
        let audio = native::check_audio(&staged_output, args.rate, frames)?;
        for (field, value) in [("peak", audio.peak), ("rms", audio.rms)] {
            let reported = result[field]
                .as_f64()
                .context("Native audio metric missing")?;
            if !reported.is_finite() || (reported - value).abs() > 1e-12 * value.abs().max(1.0) {
                bail!("Native {field} differs from decoded WAV");
            }
        }
        if result["samples_above_unity"].as_u64() != Some(audio.above_unity) {
            bail!("Native peak count mismatch");
        }
        output_hash = audio.hash;
        measured =
            json!({"peak":audio.peak,"rms":audio.rms,"samples_above_unity":audio.above_unity});
    } else {
        let state: Value = serde_json::from_slice(&read_preset(&staged_output)?)?;
        if state["synth_version"].as_str() != Some(VERSION) || !state["settings"].is_object() {
            bail!("Native saved preset version/settings mismatch");
        }
        for (name, value) in &values {
            if state["settings"][name] != *value {
                bail!("Native saved control mismatch: {name}");
            }
        }
        output_hash = native::file_hash(&staged_output)?;
    }
    if native::file_hash(&executable)? != executable_hash {
        bail!("Native executable changed during operation");
    }
    let output = destination.unwrap();
    let sidecar = sidecar.unwrap();
    let manifest = json!({"schema_version":1,"engine":"vital","backend":"native","source_commit":PIN,
        "engine_version":VERSION,"executable":executable,"executable_sha256":executable_hash,
        "action":action,"input_preset":input_path,"input_sha256":input_hash,
        "parameter_overrides":edits,"preset_parameter_values":values,"seed":args.seed,
        "output":output,"output_sha256":output_hash,"manifest":sidecar,"native":result,"measured":measured,
        "duration_seconds":if action=="render" {Some(args.duration)} else {None},
        "tail_seconds":if action=="render" {Some(args.tail)} else {None},
        "initialization":{"personal_startup_config":false,"preroll_frames":0},
        "timing_policy":"up_to_64_frames_split_at_note_off_and_output_end_no_compensation"});
    let manifest_path = staging_path.join("manifest.json");
    let mut file = File::create(&manifest_path)?;
    serde_json::to_writer_pretty(&mut file, &manifest)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    drop(file);
    File::options()
        .write(true)
        .open(&staged_output)?
        .sync_all()?;
    native::publish(
        tempfile::TempPath::try_from_path(staged_output)?,
        tempfile::TempPath::try_from_path(manifest_path)?,
        &output,
        &sidecar,
    )?;
    println!("{}", serde_json::to_string_pretty(&manifest)?);
    Ok(())
}

#[cfg(test)]
mod tests;
