fn main() {
    if let Err(error) = nzrdsp_cli::run_vital() {
        eprintln!("{error:#}");
        std::process::exit(1);
    }
}
