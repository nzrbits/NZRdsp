fn main() {
    if let Err(error) = nzrdsp_cli::run(false) {
        eprintln!("{error:#}");
        std::process::exit(1);
    }
}
