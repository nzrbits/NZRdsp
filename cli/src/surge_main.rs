fn main() {
    if let Err(error) = nzrdsp_cli::run(true) {
        eprintln!("{error:#}");
        std::process::exit(1);
    }
}
