use anyhow::{bail, Context, Result};
use clap::Args;
use serde_json::json;
use std::{path::PathBuf, time::Duration};

#[derive(Args)]
pub struct VstArgs {
    #[arg(long)]
    runtime: Option<PathBuf>,
    #[arg(long)]
    plugin: PathBuf,
    /// Name within a multi-plugin container
    #[arg(long)]
    name: Option<String>,
    /// VST3 .vstpreset state, not a vendor-specific preset container
    #[arg(long)]
    preset: Option<PathBuf>,
    #[arg(long, conflicts_with_all = ["output", "save_preset"])]
    list_params: bool,
    #[arg(long, conflicts_with_all = ["input", "output"])]
    save_preset: Option<PathBuf>,
    #[arg(short, long)]
    input: Option<PathBuf>,
    #[arg(short, long)]
    output: Option<PathBuf>,
    #[arg(long, default_value_t = 2.0)]
    tail: f64,
    #[arg(long, default_value_t = 512, value_parser = clap::value_parser!(u32).range(1..=8192))]
    block: u32,
    /// Raw normalized host value between 0 and 1
    #[arg(long = "param")]
    parameters: Vec<String>,
    #[arg(long, default_value_t = 120, value_parser = clap::value_parser!(u32).range(1..=3600))]
    timeout: u32,
}

pub fn run(args: VstArgs) -> Result<()> {
    if !args.tail.is_finite() || !(0.0..=60.0).contains(&args.tail) {
        bail!("Tail must be finite between 0 and 60 seconds");
    }
    let action = if args.list_params {
        "parameters"
    } else if args.save_preset.is_some() {
        "save"
    } else {
        "render"
    };
    if action == "render" && (args.input.is_none() || args.output.is_none()) {
        bail!("Effect rendering requires --input and --output");
    }
    let runtime = crate::worker::runtime(args.runtime)?;
    let python = runtime["python"]
        .as_str()
        .context("Runtime config needs python")?;
    let request = json!({"action":action,"plugin":args.plugin,"name":args.name,"preset":args.preset,
        "input":args.input,"output":args.output.or(args.save_preset),"tail":args.tail,"block":args.block,"parameters":args.parameters});
    let result = crate::worker::execute(
        python,
        include_bytes!("../../python/vst_worker.py"),
        request,
        Duration::from_secs(args.timeout as u64),
    )?;
    println!("{}", serde_json::to_string_pretty(&result)?);
    Ok(())
}
