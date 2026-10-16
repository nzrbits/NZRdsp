use anyhow::{bail, Context, Result};
use clap::Args;
use serde_json::json;
use std::{path::PathBuf, time::Duration};

#[derive(Args)]
pub struct TransientArgs {
    #[arg(long)]
    runtime: Option<PathBuf>,
    #[arg(short, long)]
    input: PathBuf,
    #[arg(short, long)]
    output: PathBuf,
    #[arg(long, default_value_t = 0.0, allow_negative_numbers = true)]
    attack_db: f64,
    #[arg(long, default_value_t = 0.0, allow_negative_numbers = true)]
    sustain_db: f64,
    #[arg(long, default_value_t = 0.0, allow_negative_numbers = true)]
    output_db: f64,
    #[arg(long, default_value_t=512,value_parser=clap::value_parser!(u32).range(1..=8192))]
    block: u32,
    #[arg(long, default_value_t=120,value_parser=clap::value_parser!(u32).range(1..=3600))]
    timeout: u32,
}

pub fn run(args: TransientArgs) -> Result<()> {
    for (name, value, low, high) in [
        ("Attack", args.attack_db, -15.0, 15.0),
        ("Sustain", args.sustain_db, -24.0, 24.0),
        ("Output", args.output_db, -60.0, 24.0),
    ] {
        if !value.is_finite() || !(low..=high).contains(&value) {
            bail!("{name} must be finite in {low}..{high}");
        }
    }
    let runtime = crate::worker::runtime(args.runtime)?;
    let python = runtime["python"]
        .as_str()
        .context("Runtime config needs python")?;
    let result = crate::worker::execute(
        python,
        include_bytes!("../../python/transient_worker.py"),
        json!({"input":args.input,"output":args.output,"attack_db":args.attack_db,"sustain_db":args.sustain_db,"output_db":args.output_db,"block":args.block}),
        Duration::from_secs(u64::from(args.timeout)),
    )?;
    println!("{}", serde_json::to_string_pretty(&result)?);
    Ok(())
}
