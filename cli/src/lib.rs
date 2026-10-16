use anyhow::{bail, Context, Result};
use clap::{Args, Parser, Subcommand};
use serde_json::json;
use std::{path::PathBuf, time::Duration};
mod dexed;
mod native;
mod vital;
mod vst;
mod worker;

#[derive(Parser)]
#[command(name = "nzrdsp", version, about = "NZRdsp offline synthesis")]
struct Cli {
    #[command(subcommand)]
    engine: Engine,
}

#[derive(Subcommand)]
enum Engine {
    Surge(SurgeArgs),
    /// Inspect/edit DX7 SysEx and render through the native Dexed engine
    Dexed(dexed::DexedArgs),
    /// Render and edit native presets through the pinned spectral engine
    Vital(vital::VitalArgs),
    /// Render through an installed native VST3 effect
    Vst(vst::VstArgs),
}

#[derive(Parser)]
#[command(name = "dexed-cli", version)]
struct DexedCli {
    #[command(flatten)]
    args: dexed::DexedArgs,
}

pub fn run_dexed() -> Result<()> {
    dexed::run(DexedCli::parse().args)
}

#[derive(Parser)]
#[command(name = "vital-cli", version)]
struct VitalCli {
    #[command(flatten)]
    args: vital::VitalArgs,
}

pub fn run_vital() -> Result<()> {
    vital::run(VitalCli::parse().args)
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
    /// Read the stored metadata of --preset without loading the synth
    #[arg(long, requires = "preset", conflicts_with_all = ["output", "list_params", "init_preset", "list_presets"])]
    metadata: bool,
    /// Recursively list native patches with metadata
    #[arg(long, conflicts_with_all = ["preset", "output", "list_params", "init_preset"])]
    list_presets: Option<PathBuf>,
    /// Case-insensitive metadata/path search for --list-presets
    #[arg(long, requires = "list_presets")]
    query: Option<String>,
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
            Engine::Vst(args) => return vst::run(args),
            Engine::Dexed(args) => return dexed::run(args),
            Engine::Vital(args) => return vital::run(args),
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
    let runtime = worker::runtime(args.runtime)?;
    let python = runtime["python"]
        .as_str()
        .context("Runtime config needs python")?;
    let action = if args.list_presets.is_some() {
        "catalog"
    } else if args.metadata {
        "metadata"
    } else if args.init_preset.is_some() {
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
        "parameters":args.parameters,"root":args.list_presets,"query":args.query});
    let response = worker::execute(
        python,
        include_bytes!("../../python/surge_worker.py"),
        request,
        Duration::from_secs(1800),
    )?;
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
        let Engine::Surge(args) = primary.engine else {
            panic!("Expected Surge command");
        };
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
            vec!["surge-cli", "--metadata"],
            vec!["surge-cli", "--query", "bass"],
            vec!["surge-cli", "--list-presets", ".", "-o", "out.wav"],
        ] {
            assert!(SurgeCli::try_parse_from(arguments).is_err());
        }
    }

    #[test]
    fn native_metric_decimals_keep_their_exact_f64_value() {
        let value: serde_json::Value = serde_json::from_str("0.38500484824180603").unwrap();
        assert_eq!(
            value.as_f64().unwrap().to_bits(),
            0.38500484824180603f64.to_bits()
        );
    }
}
