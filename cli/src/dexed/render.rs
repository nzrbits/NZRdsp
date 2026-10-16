use super::{name, single_voice, Bank, DexedArgs, Program};
use crate::{
    native::{check_audio, file_hash, frames, publish},
    worker,
};
use anyhow::{bail, Context, Result};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::Write,
    path::{Path, PathBuf},
    process::Command,
    time::Duration,
};

const PIN: &str = "fea2f0f0f2c397799f59b27eb11c038cd34b22c5";

pub(super) fn run(
    args: &DexedArgs,
    input: &Path,
    source: &[u8],
    bank: &Bank,
    program: &Program,
) -> Result<Value> {
    let duration = frames(args.duration, args.rate, 600.0, true)?;
    let tail = frames(args.tail, args.rate, 60.0, false)?;
    let total = duration
        .checked_add(tail)
        .context("Render frame count overflow")?;
    let config = worker::runtime(args.runtime.clone())?;
    let engine = &config["engines"]["dexed"];
    if engine["source_commit"].as_str() != Some(PIN) {
        bail!("Dexed runtime source pin mismatch");
    }
    let executable = PathBuf::from(
        engine["executable"]
            .as_str()
            .context("Configure engines.dexed.executable with build-dexed.ps1")?,
    )
    .canonicalize()?;
    let expected = engine["executable_sha256"]
        .as_str()
        .context("Dexed runtime needs executable_sha256")?;
    let native_hash = file_hash(&executable)?;
    if !native_hash.eq_ignore_ascii_case(expected) {
        bail!("Dexed executable SHA256 mismatch");
    }
    let output = args.output.as_ref().unwrap();
    let output = if output.is_absolute() {
        output.clone()
    } else {
        std::env::current_dir()?.join(output)
    };
    let mut sidecar_name = output.as_os_str().to_os_string();
    sidecar_name.push(".json");
    let sidecar = PathBuf::from(sidecar_name);
    if output.try_exists()? || sidecar.try_exists()? {
        bail!("WAV and manifest destinations must both be new");
    }
    let input = input.canonicalize()?;
    let parent = output.parent().context("Output needs a parent directory")?;
    fs::create_dir_all(parent)?;
    let staging = tempfile::Builder::new()
        .prefix(".nzrdsp-dexed-")
        .tempdir_in(parent)?;
    let voice = staging.path().join("voice.syx");
    let staged_audio = staging.path().join("render.wav");
    let staged_manifest = staging.path().join("render.json");
    let edited_sysex = single_voice(program, bank.channel);
    fs::write(&voice, &edited_sysex)?;
    let mut command = Command::new(&executable);
    command.arg(&voice).arg(&staged_audio).args([
        args.note.to_string(),
        args.velocity.to_string(),
        duration.to_string(),
        tail.to_string(),
        args.rate.to_string(),
        args.block.to_string(),
        args.engine.id().to_string(),
        args.seed.to_string(),
    ]);
    let native = worker::execute_native(command, Duration::from_secs(u64::from(args.timeout)))?;
    for (field, value) in [
        ("frames", total),
        ("sample_rate", args.rate),
        ("channels", 2),
        ("duration_frames", duration),
        ("tail_frames", tail),
        ("note", u32::from(args.note)),
        ("velocity", u32::from(args.velocity)),
        ("block", args.block),
        ("engine", u32::from(args.engine.id())),
        ("seed", args.seed),
        ("native_quantum", 64),
    ] {
        if native[field].as_u64() != Some(u64::from(value)) {
            bail!("Native render metadata mismatch: {field}");
        }
    }
    if native["source_commit"].as_str() != Some(PIN) || native["format"].as_str() != Some("float32")
    {
        bail!("Native render version/format mismatch");
    }
    let audio = check_audio(&staged_audio, args.rate, total)?;
    for (field, value) in [("peak", audio.peak), ("rms", audio.rms)] {
        let metric = native[field]
            .as_f64()
            .with_context(|| format!("Native {field} missing"))?;
        if !metric.is_finite() || (metric - value).abs() > 1e-12 * value.abs().max(1.0) {
            bail!("Native {field} differs from decoded WAV");
        }
    }
    let manifest = json!({
        "schema_version":1,"engine":"dexed","backend":"native","source_commit":PIN,
        "executable":executable,"executable_sha256":native_hash,
        "input_sysex":input,"input_sha256":format!("{:x}",Sha256::digest(source)),
        "input_format":bank.format,"source_midi_channel":bank.channel+1,"render_midi_channel":1,
        "voice":args.voice,"name":name(program),"program_bytes":&program[..],
        "edited_sysex_sha256":format!("{:x}",Sha256::digest(&edited_sysex)),
        "duration_seconds":args.duration,"tail_seconds":args.tail,
        "output":output,"output_sha256":audio.hash,"manifest":sidecar,
        "native":native,"peak":audio.peak,"rms":audio.rms,
        "initialization":{"preferences":false,"startup_cartridges":false,"external_mts":false},
        "latency_policy":"native_64_sample_quantum_no_compensation"
    });
    let mut file = File::create(&staged_manifest)?;
    serde_json::to_writer_pretty(&mut file, &manifest)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    drop(file);
    fs::OpenOptions::new()
        .write(true)
        .open(&staged_audio)?
        .sync_all()?;
    publish(
        tempfile::TempPath::try_from_path(staged_audio)?,
        tempfile::TempPath::try_from_path(staged_manifest)?,
        &output,
        &sidecar,
    )?;
    Ok(manifest)
}
