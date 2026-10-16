fn main() {
    if let Err(error) = nzrdsp_cli::run_dexed() {
        eprintln!("{error:#}");
        std::process::exit(1);
    }
}
