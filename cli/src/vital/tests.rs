use super::*;
use clap::Parser;

#[test]
fn primary_and_alias_parse_render_and_preset_operations_consistently() {
    let primary = crate::Cli::try_parse_from([
        "nzrdsp",
        "vital",
        "--param",
        "osc_1_transpose=-12",
        "--bpm",
        "176",
        "-o",
        "test.wav",
    ])
    .unwrap();
    let crate::Engine::Vital(args) = primary.engine else {
        panic!("Expected spectral command");
    };
    let alias = crate::VitalCli::try_parse_from([
        "vital-cli",
        "--param",
        "osc_1_transpose=-12",
        "--bpm",
        "176",
        "-o",
        "test.wav",
    ])
    .unwrap();
    assert_eq!(args.parameters, alias.args.parameters);
    assert_eq!(args.bpm, 176.0);
    assert!(crate::VitalCli::try_parse_from(["vital-cli", "--list-params"]).is_ok());
    assert!(crate::VitalCli::try_parse_from(["vital-cli", "--save-preset", "test.vital"]).is_ok());
    for flags in [
        vec!["--note", "128", "-o", "a.wav"],
        vec!["--velocity", "0", "-o", "a.wav"],
        vec!["--rate", "7999", "-o", "a.wav"],
        vec!["--seed", "1000000001", "-o", "a.wav"],
        vec!["--list-params", "-o", "a.wav"],
        vec!["--save-preset", "a.vital", "-o", "a.wav"],
        vec!["--list-params", "--save-preset", "a.vital"],
        vec!["--note", "60", "--list-params"],
    ] {
        assert!(
            crate::VitalCli::try_parse_from(
                std::iter::once("vital-cli").chain(flags.iter().copied())
            )
            .is_err(),
            "accepted {flags:?}"
        );
    }
}

#[test]
fn assignments_reject_duplicates_nonfinite_and_float_overflow() {
    for input in [
        vec!["a=1", "a=2"],
        vec!["a=NaN"],
        vec!["a=inf"],
        vec!["a=1e40"],
        vec!["=1"],
        vec!["a"],
        vec!["a="],
    ] {
        assert!(assignments(&input.into_iter().map(String::from).collect::<Vec<_>>()).is_err());
    }
    let valid = assignments(&["osc_1_transpose=-12".into(), "osc_1_on=1".into()]).unwrap();
    assert_eq!(valid["osc_1_transpose"], -12.0);
}

#[test]
fn raw_indexed_edits_are_checked_before_float_rounding() {
    let mut metadata = json!({"parameter_count":772,"parameters":{}});
    for index in 0..772 {
        metadata["parameters"][format!("control-{index}")] =
            json!({"min":0,"max":1,"value":0,"indexed":true});
    }
    let invalid = assignments(&["control-0=0.9999999999999".into()]).unwrap();
    assert!(validate_edits(&metadata, &invalid).is_err());
    let valid = assignments(&["control-0=1".into()]).unwrap();
    assert_eq!(validate_edits(&metadata, &valid).unwrap()["control-0"], 1.0);
    assert!(validate_edits(&metadata, &assignments(&["unknown=0".into()]).unwrap()).is_err());
}
