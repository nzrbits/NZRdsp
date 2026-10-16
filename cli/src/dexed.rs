use anyhow::{bail, Context, Result};
use clap::Args;
use serde_json::{json, Value};
use std::{
    fs::File,
    io::{Read, Write},
    path::{Path, PathBuf},
};

#[derive(Args)]
pub struct DexedArgs {
    /// One complete Yamaha DX7 32-voice bank or single-voice SysEx message
    #[arg(long)]
    sysex: Option<PathBuf>,
    #[arg(long, default_value_t = 0, value_parser = clap::value_parser!(u8).range(0..=31))]
    voice: u8,
    /// Print the 155 unpacked byte definitions without loading a file
    #[arg(long, conflicts_with_all = ["sysex", "voice", "list_voices", "parameters", "name", "save_sysex"])]
    list_params: bool,
    #[arg(long, requires = "sysex", conflicts_with_all = ["voice", "parameters", "name", "save_sysex"])]
    list_voices: bool,
    /// Raw integer assignment, e.g. algorithm=31 or op1.output_level=99
    #[arg(long = "param", requires = "sysex")]
    parameters: Vec<String>,
    /// Up to ten printable ASCII characters, padded with spaces
    #[arg(long, requires = "sysex")]
    name: Option<String>,
    /// Export the selected/edited voice as a new single-voice .syx file
    #[arg(long, requires = "sysex")]
    save_sysex: Option<PathBuf>,
}

#[derive(Clone)]
struct Parameter {
    key: String,
    offset: usize,
    max: u8,
}

fn parameters() -> Vec<Parameter> {
    let fields = [
        ("eg_rate1", 99),
        ("eg_rate2", 99),
        ("eg_rate3", 99),
        ("eg_rate4", 99),
        ("eg_level1", 99),
        ("eg_level2", 99),
        ("eg_level3", 99),
        ("eg_level4", 99),
        ("key_breakpoint", 99),
        ("key_left_depth", 99),
        ("key_right_depth", 99),
        ("key_left_curve", 3),
        ("key_right_curve", 3),
        ("rate_scaling", 7),
        ("amp_mod_sensitivity", 3),
        ("key_velocity_sensitivity", 7),
        ("output_level", 99),
        ("osc_mode", 1),
        ("frequency_coarse", 31),
        ("frequency_fine", 99),
        ("detune", 14),
    ];
    let mut result = Vec::with_capacity(155);
    for stored in 0..6 {
        for (field, max) in fields {
            result.push(Parameter {
                key: format!("op{}.{}", 6 - stored, field),
                offset: result.len(),
                max,
            });
        }
    }
    for field in ["rate", "level"] {
        for index in 1..=4 {
            result.push(Parameter {
                key: format!("pitch_eg.{field}{index}"),
                offset: result.len(),
                max: 99,
            });
        }
    }
    for (key, max) in [
        ("algorithm", 31),
        ("feedback", 7),
        ("osc_key_sync", 1),
        ("lfo.speed", 99),
        ("lfo.delay", 99),
        ("lfo.pitch_mod_depth", 99),
        ("lfo.amp_mod_depth", 99),
        ("lfo.key_sync", 1),
        ("lfo.wave", 5),
        ("lfo.pitch_mod_sensitivity", 7),
        ("transpose", 48),
    ] {
        result.push(Parameter {
            key: key.into(),
            offset: result.len(),
            max,
        });
    }
    for index in 1..=10 {
        result.push(Parameter {
            key: format!("name.byte{index}"),
            offset: result.len(),
            max: 127,
        });
    }
    result
}

type Program = [u8; 155];
struct Bank {
    channel: u8,
    format: &'static str,
    voices: Vec<Program>,
}

fn checksum(payload: &[u8]) -> u8 {
    (0u8.wrapping_sub(
        payload
            .iter()
            .fold(0u8, |sum, &value| sum.wrapping_add(value)),
    )) & 127
}

fn validate(program: &Program) -> Result<()> {
    for parameter in parameters() {
        if program[parameter.offset] > parameter.max {
            bail!(
                "{} at byte {} exceeds raw maximum {}: {}",
                parameter.key,
                parameter.offset,
                parameter.max,
                program[parameter.offset]
            );
        }
    }
    Ok(())
}

fn unpack(packed: &[u8]) -> Program {
    let mut voice = [0; 155];
    for op in 0..6 {
        let source = &packed[op * 17..op * 17 + 17];
        let target = &mut voice[op * 21..op * 21 + 21];
        target[..11].copy_from_slice(&source[..11]);
        target[11] = source[11] & 3;
        target[12] = (source[11] >> 2) & 3;
        target[13] = source[12] & 7;
        target[14] = source[13] & 3;
        target[15] = (source[13] >> 2) & 7;
        target[16] = source[14];
        target[17] = source[15] & 1;
        target[18] = (source[15] >> 1) & 31;
        target[19] = source[16];
        target[20] = source[12] >> 3;
    }
    voice[126..135].copy_from_slice(&packed[102..111]);
    voice[134] &= 31;
    voice[135] = packed[111] & 7;
    voice[136] = (packed[111] >> 3) & 1;
    voice[137..141].copy_from_slice(&packed[112..116]);
    voice[141] = packed[116] & 1;
    voice[142] = (packed[116] >> 1) & 7;
    voice[143] = (packed[116] >> 4) & 7;
    voice[144..155].copy_from_slice(&packed[117..128]);
    voice
}

fn parse(bytes: &[u8]) -> Result<Bank> {
    if bytes.len() < 8
        || bytes[0] != 0xf0
        || bytes[1] != 0x43
        || bytes[2] > 15
        || *bytes.last().unwrap() != 0xf7
    {
        bail!("Expected one Yamaha DX7 bulk SysEx message (F0 43 0n ... F7)");
    }
    if bytes[3..bytes.len() - 1].iter().any(|byte| *byte > 127) {
        bail!("SysEx data must be 7-bit");
    }
    let (payload_len, format) = match bytes[3] {
        0 if bytes.len() == 163 && bytes[4..6] == [1, 27] => (155, "single_voice"),
        9 if bytes.len() == 4104 && bytes[4..6] == [32, 0] => (4096, "32_voice_bank"),
        _ => bail!("Unsupported DX7 SysEx format, byte count or message length"),
    };
    let payload = &bytes[6..6 + payload_len];
    if checksum(payload) != bytes[6 + payload_len] {
        bail!("DX7 SysEx checksum mismatch");
    }
    let voices = if payload_len == 155 {
        vec![payload.try_into().unwrap()]
    } else {
        payload.chunks_exact(128).map(unpack).collect()
    };
    for (index, voice) in voices.iter().enumerate() {
        validate(voice).with_context(|| format!("Voice {index}"))?;
    }
    Ok(Bank {
        channel: bytes[2],
        format,
        voices,
    })
}

fn single_voice(program: &Program, channel: u8) -> Vec<u8> {
    let mut bytes = vec![0xf0, 0x43, channel, 0, 1, 27];
    bytes.extend_from_slice(program);
    bytes.extend([checksum(program), 0xf7]);
    bytes
}

fn name(program: &Program) -> String {
    program[145..]
        .iter()
        .map(|&value| {
            if (32..127).contains(&value) {
                value as char
            } else {
                '\u{fffd}'
            }
        })
        .collect::<String>()
        .trim_end()
        .into()
}

fn edit(program: &mut Program, assignments: &[String], new_name: Option<&str>) -> Result<()> {
    let definitions = parameters();
    let mut staged = *program;
    let mut seen = std::collections::HashSet::new();
    if let Some(name) = new_name {
        if name.len() > 10 || !name.bytes().all(|b| (32..127).contains(&b)) {
            bail!("Name must contain up to ten printable ASCII bytes");
        }
        staged[145..].fill(b' ');
        staged[145..145 + name.len()].copy_from_slice(name.as_bytes());
        seen.extend(145..155);
    }
    for assignment in assignments {
        let (key, value) = assignment
            .split_once('=')
            .context("Parameters use key=raw_integer")?;
        let parameter = definitions
            .iter()
            .find(|p| p.key == key)
            .with_context(|| format!("Unknown DX7 parameter {key}"))?;
        if !seen.insert(parameter.offset) {
            bail!("Duplicate or conflicting parameter {key}");
        }
        let value: u8 = value
            .parse()
            .with_context(|| format!("Invalid raw integer for {key}"))?;
        if value > parameter.max {
            bail!("{key} exceeds raw maximum {}", parameter.max);
        }
        staged[parameter.offset] = value;
    }
    validate(&staged)?;
    *program = staged;
    Ok(())
}

fn save_new(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let mut file = tempfile::NamedTempFile::new_in(parent)?;
    file.write_all(bytes)?;
    file.as_file().sync_all()?;
    file.persist_noclobber(path)
        .map_err(|e| e.error)
        .context("Export requires a new destination")?;
    Ok(())
}

pub fn run(args: DexedArgs) -> Result<()> {
    let definitions = parameters();
    if args.list_params {
        let list: Vec<_> = definitions
            .iter()
            .map(|p| json!({"key":p.key,"offset":p.offset,"min":0,"max":p.max}))
            .collect();
        println!(
            "{}",
            serde_json::to_string_pretty(
                &json!({"engine":"dexed","action":"sysex_parameter_schema","raw_bytes":155,"synth_controls":145,"name_bytes":10,"parameters":list})
            )?
        );
        return Ok(());
    }
    let input = args
        .sysex
        .context("Pass --sysex or --list-params; Dexed audio rendering is not implemented yet")?;
    let mut bytes = Vec::new();
    File::open(&input)?.take(4105).read_to_end(&mut bytes)?;
    let bank = parse(&bytes)?;
    let response: Value = if args.list_voices {
        json!({"input":input,"format":bank.format,"midi_channel":bank.channel + 1,
            "voices":bank.voices.iter().enumerate().map(|(index, voice)|json!({"voice":index,"name":name(voice)})).collect::<Vec<_>>()})
    } else {
        let mut program = *bank
            .voices
            .get(args.voice as usize)
            .context("Voice index does not exist in this SysEx message")?;
        edit(&mut program, &args.parameters, args.name.as_deref())?;
        if let Some(path) = &args.save_sysex {
            save_new(path, &single_voice(&program, bank.channel))?;
        }
        let values: Vec<_> = definitions.iter().map(|p| json!({"key":p.key,"offset":p.offset,"raw":program[p.offset],"min":0,"max":p.max})).collect();
        json!({"input":input,"format":bank.format,"midi_channel":bank.channel+1,"voice":args.voice,
            "name":name(&program),"algorithm_display":program[134]+1,"program_bytes":&program[..],
            "parameters":values,"saved_sysex":args.save_sysex})
    };
    println!("{}", serde_json::to_string_pretty(&response)?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn blank() -> Program {
        let mut voice = [0; 155];
        voice[145..].fill(b' ');
        voice
    }

    fn packed_bank() -> Vec<u8> {
        let mut payload = vec![0; 4096];
        for index in 0..32 {
            let voice = &mut payload[index * 128..(index + 1) * 128];
            for op in 0..6 {
                voice[op * 17 + 11] = 2 | (3 << 2);
                voice[op * 17 + 12] = 5 | (14 << 3);
                voice[op * 17 + 13] = 3 | (7 << 2);
                voice[op * 17 + 14] = index as u8;
                voice[op * 17 + 15] = 1 | (31 << 1);
                voice[op * 17 + 16] = 99;
            }
            voice[110] = index as u8;
            voice[111] = 7 | (1 << 3);
            voice[116] = 1 | (5 << 1) | (7 << 4);
            voice[117] = 48;
            voice[118..128].copy_from_slice(b"TEST VOICE");
        }
        let mut bytes = vec![0xf0, 0x43, 15, 9, 32, 0];
        bytes.extend_from_slice(&payload);
        bytes.extend([checksum(&payload), 0xf7]);
        bytes
    }

    #[test]
    fn all_155_offsets_are_unique_and_single_voice_roundtrips_every_maximum() {
        let definitions = parameters();
        assert_eq!(definitions.len(), 155);
        let mut keys = std::collections::HashSet::new();
        let mut program = blank();
        for (offset, definition) in definitions.iter().enumerate() {
            assert_eq!(definition.offset, offset);
            assert!(keys.insert(&definition.key));
            program[offset] = definition.max;
        }
        for channel in 0..16 {
            let bytes = single_voice(&program, channel);
            let bank = parse(&bytes).unwrap();
            assert_eq!(bank.channel, channel);
            assert_eq!(bank.voices, [program]);
        }
    }

    #[test]
    fn bank_unpacks_operator_bitfields_and_all_voice_indices() {
        let bank = parse(&packed_bank()).unwrap();
        assert_eq!(bank.voices.len(), 32);
        for (index, voice) in bank.voices.iter().enumerate() {
            assert_eq!(voice[134], index as u8);
            for op in 0..6 {
                assert_eq!(
                    &voice[op * 21 + 11..op * 21 + 21],
                    &[2, 3, 5, 3, 7, index as u8, 1, 31, 99, 14]
                );
            }
            assert_eq!(&voice[135..137], &[7, 1]);
            assert_eq!(&voice[141..145], &[1, 5, 7, 48]);
            assert_eq!(name(voice), "TEST VOICE");
            assert_eq!(
                parse(&single_voice(voice, bank.channel)).unwrap().voices[0],
                *voice
            );
        }
    }

    #[test]
    fn corruption_truncation_trailing_data_and_invalid_values_are_rejected() {
        let original = single_voice(&blank(), 0);
        for length in 0..original.len() {
            assert!(parse(&original[..length]).is_err());
        }
        for (index, value) in [
            (0, 0),
            (1, 0),
            (2, 16),
            (3, 9),
            (4, 0),
            (5, 0),
            (6, 128),
            (161, 1),
            (162, 0),
        ] {
            let mut bad = original.clone();
            bad[index] = value;
            assert!(parse(&bad).is_err(), "byte {index}");
        }
        let mut trailing = original.clone();
        trailing.push(0);
        assert!(parse(&trailing).is_err());
        for parameter in parameters() {
            if parameter.max == 127 {
                continue;
            }
            let mut invalid = blank();
            invalid[parameter.offset] = parameter.max + 1;
            assert!(
                parse(&single_voice(&invalid, 0)).is_err(),
                "{}",
                parameter.key
            );
        }
        let mut bad_bank = packed_bank();
        bad_bank[6 + 31 * 128 + 117] = 49;
        bad_bank[4102] = checksum(&bad_bank[6..4102]);
        assert!(parse(&bad_bank).is_err());
    }

    #[test]
    fn edits_are_transactional_named_and_preserve_existing_exports() {
        let mut program = blank();
        edit(
            &mut program,
            &[
                "op6.detune=14".into(),
                "op1.output_level=99".into(),
                "algorithm=31".into(),
            ],
            Some("NEURO"),
        )
        .unwrap();
        assert_eq!(program[20], 14);
        assert_eq!(program[121], 99);
        assert_eq!(program[134], 31);
        assert_eq!(&program[145..], b"NEURO     ");
        let before = program;
        for bad in [
            "algorithm=32",
            "op1.osc_mode=2",
            "algorithm=-1",
            "algorithm=1.5",
            "unknown=1",
        ] {
            assert!(edit(&mut program, &["algorithm=0".into(), bad.into()], None).is_err());
            assert_eq!(program, before);
        }
        assert!(edit(&mut program, &["name.byte1=65".into()], Some("NAME")).is_err());
        for bad in ["12345678901", "\u{fc}mlaut", "newline\n"] {
            assert!(edit(&mut program, &[], Some(bad)).is_err());
        }
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("voice.syx");
        save_new(&path, &single_voice(&program, 0)).unwrap();
        let original = std::fs::read(&path).unwrap();
        assert!(save_new(&path, b"overwrite").is_err());
        assert_eq!(std::fs::read(&path).unwrap(), original);
        assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 1);
    }
}
