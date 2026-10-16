use super::{name, single_voice, Bank, DexedArgs, Program};
use crate::worker;
use anyhow::{bail, Context, Result};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::{Read, Write},
    path::{Path, PathBuf},
    process::Command,
    time::Duration,
};

const PIN: &str = "fea2f0f0f2c397799f59b27eb11c038cd34b22c5";

fn file_hash(path: &Path) -> Result<String> {
    let mut input = File::open(path)?;
    let mut hash = Sha256::new();
    let mut bytes = [0u8; 65536];
    loop {
        let count = input.read(&mut bytes)?;
        if count == 0 {
            break;
        }
        hash.update(&bytes[..count]);
    }
    Ok(format!("{:x}", hash.finalize()))
}

fn frames(seconds: f64, rate: u32, max: f64, positive: bool) -> Result<u32> {
    if !seconds.is_finite() || !(0.0..=max).contains(&seconds) || (positive && seconds == 0.0) {
        bail!("Duration must be finite in (0, 600] seconds; tail in [0, 60]");
    }
    let frames = (seconds * rate as f64).round() as u32;
    if positive && frames == 0 {
        bail!("Duration rounds to zero frames");
    }
    Ok(frames)
}

struct AudioCheck {
    hash: String,
    peak: f64,
    rms: f64,
}
fn check_audio(path: &Path, rate: u32, frames: u32) -> Result<AudioCheck> {
    let mut file = File::open(path)?;
    let expected_bytes = u64::from(frames) * 8;
    if file.metadata()?.len() != 56 + expected_bytes {
        bail!("Native WAV byte count differs from the requested frame count");
    }
    let mut header = [0; 56];
    file.read_exact(&mut header)?;
    let u32_at = |offset: usize| u32::from_le_bytes(header[offset..offset + 4].try_into().unwrap());
    let u16_at = |offset: usize| u16::from_le_bytes(header[offset..offset + 2].try_into().unwrap());
    if &header[..4] != b"RIFF"
        || u32_at(4) as u64 != 48 + expected_bytes
        || &header[8..12] != b"WAVE"
        || &header[12..16] != b"fmt "
        || u32_at(16) != 16
        || u16_at(20) != 3
        || u16_at(22) != 2
        || u32_at(24) != rate
        || u32_at(28) != rate * 8
        || u16_at(32) != 8
        || u16_at(34) != 32
        || &header[36..40] != b"fact"
        || u32_at(40) != 4
        || u32_at(44) != frames
        || &header[48..52] != b"data"
        || u32_at(52) as u64 != expected_bytes
    {
        bail!("Native WAV header differs from the requested Float32 stereo format");
    }
    let mut hash = Sha256::new();
    hash.update(header);
    let mut remaining = expected_bytes;
    let mut buffer = [0; 65536];
    let mut peak = 0.0f64;
    let mut energy = 0.0f64;
    while remaining > 0 {
        let count = remaining.min(buffer.len() as u64) as usize;
        file.read_exact(&mut buffer[..count])?;
        hash.update(&buffer[..count]);
        for bytes in buffer[..count].chunks_exact(4) {
            let sample = f32::from_le_bytes(bytes.try_into().unwrap()) as f64;
            if !sample.is_finite() {
                bail!("Native WAV contains non-finite samples");
            }
            peak = peak.max(sample.abs());
            energy += sample * sample;
        }
        remaining -= count as u64;
    }
    Ok(AudioCheck {
        hash: format!("{:x}", hash.finalize()),
        peak,
        rms: (energy / (f64::from(frames) * 2.0)).sqrt(),
    })
}

fn publish(
    audio: tempfile::TempPath,
    manifest: tempfile::TempPath,
    output: &Path,
    sidecar: &Path,
) -> Result<()> {
    manifest
        .persist_noclobber(sidecar)
        .map_err(|e| e.error)
        .context("Manifest destination must be new")?;
    if let Err(error) = audio.persist_noclobber(output) {
        fs::remove_file(sidecar)
            .context("Roll back newly published manifest after WAV publication failed")?;
        return Err(error.error).context("WAV destination must be new");
    }
    Ok(())
}

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

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn frame_validation_rejects_zero_nonfinite_and_out_of_bounds() {
        assert_eq!(frames(0.123456, 48000, 600.0, true).unwrap(), 5926);
        for value in [0.0, -1.0, f64::NAN, f64::INFINITY, 601.0, 1e-12] {
            assert!(frames(value, 48000, 600.0, true).is_err());
        }
        assert_eq!(frames(0.0, 48000, 60.0, false).unwrap(), 0);
        assert!(frames(61.0, 48000, 60.0, false).is_err());
    }
    #[test]
    fn failed_audio_publication_removes_its_manifest_and_preserves_existing_audio() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("keep.wav");
        let sidecar = directory.path().join("keep.wav.json");
        fs::write(&output, b"existing").unwrap();
        let mut audio = tempfile::NamedTempFile::new_in(directory.path()).unwrap();
        audio.write_all(b"new").unwrap();
        let mut manifest = tempfile::NamedTempFile::new_in(directory.path()).unwrap();
        manifest.write_all(b"new manifest").unwrap();
        assert!(publish(
            audio.into_temp_path(),
            manifest.into_temp_path(),
            &output,
            &sidecar
        )
        .is_err());
        assert_eq!(fs::read(&output).unwrap(), b"existing");
        assert!(!sidecar.exists());
        assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 1);
    }
    #[test]
    fn native_wav_validation_rejects_nonfinite_header_and_length_errors() {
        let bytes = [
            b"RIFF".as_slice(),
            &56u32.to_le_bytes(),
            b"WAVEfmt ",
            &16u32.to_le_bytes(),
            &3u16.to_le_bytes(),
            &2u16.to_le_bytes(),
            &48000u32.to_le_bytes(),
            &384000u32.to_le_bytes(),
            &8u16.to_le_bytes(),
            &32u16.to_le_bytes(),
            b"fact",
            &4u32.to_le_bytes(),
            &1u32.to_le_bytes(),
            b"data",
            &8u32.to_le_bytes(),
            &0.25f32.to_le_bytes(),
            &(-0.5f32).to_le_bytes(),
        ]
        .concat();
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("test.wav");
        fs::write(&path, &bytes).unwrap();
        assert_eq!(check_audio(&path, 48000, 1).unwrap().peak, 0.5);
        assert!(check_audio(&path, 44100, 1).is_err());
        assert!(check_audio(&path, 48000, 2).is_err());
        let mut nonfinite = bytes.clone();
        nonfinite[56..60].copy_from_slice(&f32::NAN.to_le_bytes());
        fs::write(&path, nonfinite).unwrap();
        assert!(check_audio(&path, 48000, 1).is_err());
        let mut wrong_header = bytes.clone();
        wrong_header[22] = 1;
        fs::write(&path, wrong_header).unwrap();
        assert!(check_audio(&path, 48000, 1).is_err());
        fs::write(&path, &bytes[..63]).unwrap();
        assert!(check_audio(&path, 48000, 1).is_err());
    }
}
