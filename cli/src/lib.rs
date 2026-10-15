use anyhow::{bail, Context, Result};
use clap::{Args, Parser, Subcommand};
use serde_json::{json, Value};
use std::{
    io::Write,
    path::PathBuf,
    process::{Command, Stdio},
};

#[derive(Parser)]
#[command(name = "nzrdsp", version, about = "NZRdsp offline synthesis")]
struct Cli {
    #[command(subcommand)]
    engine: Engine,
}

#[derive(Subcommand)]
enum Engine {
    Surge(SurgeArgs),
}

#[derive(Parser)]
#[command(name = "surge-cli", version)]
struct SurgeCli {
    #[command(flatten)]
    args: SurgeArgs,
}

#[derive(Args)]
struct SurgeArgs {
    /// Runtime JSON (default: NZRDSP_RUNTIME or LocalAppData/NZRdsp/runtime.json)
    #[arg(long)]
    runtime: Option<PathBuf>,
    /// Native Surge .fxp patch; omit for the initialized engine
    #[arg(long)]
    preset: Option<PathBuf>,
    /// Save a native initialized patch to a new file
    #[arg(long, conflicts_with_all = ["preset", "output", "list_params"])]
    init_preset: Option<PathBuf>,
    /// Print native numeric parameters, paths, ranges and displays as JSON
    #[arg(long, conflicts_with = "output")]
    list_params: bool,
    #[arg(short, long)]
    output: Option<PathBuf>,
    #[arg(long, default_value_t = 36, value_parser = clap::value_parser!(u8).range(0..=127))]
    note: u8,
    #[arg(long, default_value_t = 100, value_parser = clap::value_parser!(u8).range(1..=127))]
    velocity: u8,
    #[arg(long, default_value_t = 2.0)]
    duration: f64,
    #[arg(long, default_value_t = 2.0)]
    tail: f64,
    #[arg(long, default_value_t = 48000, value_parser = clap::value_parser!(u32).range(8000..=192000))]
    rate: u32,
    /// Native raw value: --param volume=-6 (see --list-params)
    #[arg(long = "param")]
    parameters: Vec<String>,
}

pub fn run(surge_alias: bool) -> Result<()> {
    let args = if surge_alias {
        SurgeCli::parse().args
    } else {
        match Cli::parse().engine {
            Engine::Surge(args) => args,
        }
    };
    if !args.duration.is_finite()
        || !(0.0..=600.0).contains(&args.duration)
        || args.duration == 0.0
        || !args.tail.is_finite()
        || !(0.0..=60.0).contains(&args.tail)
    {
        bail!("Duration must be finite in (0, 600] seconds; tail in [0, 60]");
    }
    let runtime_path = args
        .runtime
        .or_else(|| std::env::var_os("NZRDSP_RUNTIME").map(PathBuf::from))
        .or_else(|| {
            std::env::var_os("LOCALAPPDATA")
                .map(|path| PathBuf::from(path).join("NZRdsp").join("runtime.json"))
        })
        .context("Pass --runtime or set NZRDSP_RUNTIME")?;
    let runtime: Value = serde_json::from_slice(
        &std::fs::read(&runtime_path)
            .with_context(|| format!("Read runtime config {}", runtime_path.display()))?,
    )?;
    let python = runtime["python"]
        .as_str()
        .context("Runtime config needs python")?;
    let action = if args.init_preset.is_some() {
        "init"
    } else if args.list_params {
        "parameters"
    } else {
        "render"
    };
    if action == "render" && args.output.is_none() {
        bail!("Rendering requires --output");
    }
    let request = json!({"action":action,"engine":runtime["engines"]["surge"],
        "preset":args.preset,"output":args.output.or(args.init_preset),"note":args.note,
        "velocity":args.velocity,"duration":args.duration,"tail":args.tail,"rate":args.rate,
        "parameters":args.parameters});
    let mut worker = tempfile::NamedTempFile::new()?;
    worker.write_all(include_bytes!("../../python/surge_worker.py"))?;
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
    let output = child.wait_with_output()?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let response = stdout
        .lines()
        .rev()
        .find_map(|line| serde_json::from_str::<Value>(line).ok())
        .with_context(|| {
            format!(
                "Worker returned no JSON: {}",
                String::from_utf8_lossy(&output.stderr)
            )
        })?;
    if !output.status.success() || response["error"].is_string() {
        bail!(
            "{}",
            response["error"].as_str().unwrap_or("Surge worker failed")
        );
    }
    println!("{}", serde_json::to_string_pretty(&response)?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn both_entry_points_accept_native_parameter_assignments() {
        let primary =
            Cli::try_parse_from(["nzrdsp", "surge", "--param", "volume=-6", "-o", "test.wav"])
                .unwrap();
        let Engine::Surge(args) = primary.engine;
        assert_eq!(args.parameters, ["volume=-6"]);
        let alias =
            SurgeCli::try_parse_from(["surge-cli", "--param", "volume=-6", "-o", "test.wav"])
                .unwrap();
        assert_eq!(alias.args.parameters, args.parameters);
    }

    #[test]
    fn invalid_midi_ranges_and_conflicting_outputs_are_rejected() {
        for arguments in [
            vec!["surge-cli", "--note", "128"],
            vec!["surge-cli", "--velocity", "0"],
            vec!["surge-cli", "--rate", "7999"],
            vec!["surge-cli", "--init-preset", "init.fxp", "-o", "out.wav"],
            vec!["surge-cli", "--list-params", "-o", "out.wav"],
        ] {
            assert!(SurgeCli::try_parse_from(arguments).is_err());
        }
    }
}
