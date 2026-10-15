use anyhow::{bail, Context, Result};
use serde_json::Value;
use std::{
    io::{Read, Write},
    path::PathBuf,
    process::{Command, Stdio},
    time::{Duration, Instant},
};

pub fn runtime(path: Option<PathBuf>) -> Result<Value> {
    let path = path
        .or_else(|| std::env::var_os("NZRDSP_RUNTIME").map(PathBuf::from))
        .or_else(|| {
            std::env::var_os("LOCALAPPDATA")
                .map(|p| PathBuf::from(p).join("NZRdsp").join("runtime.json"))
        })
        .context("Pass --runtime or set NZRDSP_RUNTIME")?;
    serde_json::from_slice(
        &std::fs::read(&path).with_context(|| format!("Read runtime config {}", path.display()))?,
    )
    .map_err(Into::into)
}

pub fn execute(
    python: &str,
    script: &[u8],
    mut request: Value,
    timeout: Duration,
) -> Result<Value> {
    let staging = if let Some(output) = request["output"].as_str() {
        let parent = std::path::Path::new(output)
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(std::path::Path::new("."));
        std::fs::create_dir_all(parent)?;
        tempfile::Builder::new()
            .prefix(".nzrdsp-")
            .tempdir_in(parent)?
    } else {
        tempfile::tempdir()?
    };
    request["staging_dir"] = serde_json::json!(staging.path().canonicalize()?);
    let mut worker = tempfile::NamedTempFile::new()?;
    worker.write_all(script)?;
    worker.flush()?;
    let mut command = Command::new(python);
    command
        .args(["-X", "utf8", "-u"])
        .arg(worker.path())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command
        .spawn()
        .context("Start the configured Python runtime")?;
    let mut stdout = child.stdout.take().context("Worker stdout missing")?;
    let mut stderr = child.stderr.take().context("Worker stderr missing")?;
    let output_reader = std::thread::spawn(move || {
        let mut bytes = Vec::new();
        stdout.read_to_end(&mut bytes).map(|_| bytes)
    });
    let error_reader = std::thread::spawn(move || {
        let mut bytes = Vec::new();
        stderr.read_to_end(&mut bytes).map(|_| bytes)
    });
    let write_result = child
        .stdin
        .take()
        .context("Worker stdin missing")?
        .write_all(&serde_json::to_vec(&request)?);
    if let Err(error) = write_result {
        let _ = child.kill();
        let _ = child.wait();
        return Err(error.into());
    }
    let started = Instant::now();
    let (status, timed_out) = loop {
        if let Some(status) = child.try_wait()? {
            break (status, false);
        }
        if started.elapsed() >= timeout {
            let _ = child.kill();
            break (child.wait()?, true);
        }
        std::thread::sleep(Duration::from_millis(20));
    };
    let stdout = output_reader
        .join()
        .map_err(|_| anyhow::anyhow!("Worker stdout reader failed"))??;
    let stderr = error_reader
        .join()
        .map_err(|_| anyhow::anyhow!("Worker stderr reader failed"))??;
    if timed_out {
        bail!(
            "Worker exceeded its {} second timeout",
            timeout.as_secs_f64()
        );
    }
    let text = String::from_utf8_lossy(&stdout);
    let response = text
        .lines()
        .rev()
        .find_map(|line| serde_json::from_str::<Value>(line).ok())
        .with_context(|| {
            format!(
                "Worker returned no JSON (exit {status}): {}",
                String::from_utf8_lossy(&stderr)
            )
        })?;
    if !status.success() || response["error"].is_string() {
        bail!(
            "{}",
            response["error"].as_str().unwrap_or("Native worker failed")
        );
    }
    Ok(response)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    #[ignore = "Requires NZRDSP_TEST_PYTHON; no plugins or playback"]
    fn hung_worker_is_terminated() {
        let python = std::env::var("NZRDSP_TEST_PYTHON").unwrap();
        let started = Instant::now();
        let error = execute(
            &python,
            b"import sys,time;sys.stdin.read();time.sleep(10)",
            serde_json::json!({}),
            Duration::from_millis(100),
        )
        .unwrap_err();
        assert!(error.to_string().contains("timeout"));
        assert!(started.elapsed() < Duration::from_secs(3));
    }
}
