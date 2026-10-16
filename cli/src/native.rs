use anyhow::{bail, Context, Result};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::Read,
    path::Path,
};

pub(crate) fn file_hash(path: &Path) -> Result<String> {
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

pub(crate) fn frames(seconds: f64, rate: u32, max: f64, positive: bool) -> Result<u32> {
    if !seconds.is_finite() || !(0.0..=max).contains(&seconds) || (positive && seconds == 0.0) {
        bail!("Duration must be finite in (0, 600] seconds; tail in [0, 60]");
    }
    let frames = (seconds * rate as f64).round() as u32;
    if positive && frames == 0 {
        bail!("Duration rounds to zero frames");
    }
    Ok(frames)
}

pub(crate) struct AudioCheck {
    pub hash: String,
    pub peak: f64,
    pub rms: f64,
    pub above_unity: u64,
}
pub(crate) fn check_audio(path: &Path, rate: u32, frames: u32) -> Result<AudioCheck> {
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
    let mut above_unity = 0;
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
            above_unity += u64::from(sample.abs() > 1.0);
        }
        remaining -= count as u64;
    }
    Ok(AudioCheck {
        hash: format!("{:x}", hash.finalize()),
        above_unity,
        peak,
        rms: (energy / (f64::from(frames) * 2.0)).sqrt(),
    })
}

pub(crate) fn publish(
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
            .context("Roll back newly published manifest after output publication failed")?;
        return Err(error.error).context("Output destination must be new");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
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
        assert_eq!(check_audio(&path, 48000, 1).unwrap().above_unity, 0);
        assert!(check_audio(&path, 44100, 1).is_err());
        assert!(check_audio(&path, 48000, 2).is_err());
        let mut above_unity = bytes.clone();
        above_unity[56..60].copy_from_slice(&2.0f32.to_le_bytes());
        fs::write(&path, above_unity).unwrap();
        assert_eq!(check_audio(&path, 48000, 1).unwrap().above_unity, 1);
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
