#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use base64::Engine;
use std::fs;
use std::path::PathBuf;


#[derive(Clone, serde::Serialize)]
struct PendingFiles {
    midi: Option<String>,
    sample: Option<String>,
    bpm: Option<u32>,
    tab: String,
}

#[tauri::command]
fn read_file(path: String) -> Result<(String, String), String> {
    let p = PathBuf::from(&path);
    let data = fs::read(&p).map_err(|e| e.to_string())?;
    let name = p
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_default();
    let b64 = base64::engine::general_purpose::STANDARD.encode(&data);
    Ok((b64, name))
}

#[tauri::command]
fn save_file(path: String, b64data: String) -> Result<(), String> {
    let data = base64::engine::general_purpose::STANDARD
        .decode(&b64data)
        .map_err(|e| e.to_string())?;
    fs::write(&path, &data).map_err(|e| e.to_string())
}

#[tauri::command]
fn get_pending(state: tauri::State<'_, PendingFiles>) -> PendingFiles {
    state.inner().clone()
}

fn parse_args() -> PendingFiles {
    let args: Vec<String> = std::env::args().collect();
    let mut pending = PendingFiles {
        midi: None,
        sample: None,
        bpm: None,
        tab: "synth".into(),
    };

    let mut i = 1;
    while i < args.len() {
        let a = &args[i];
        match a.as_str() {
            "--midi" | "-m" => {
                if let Some(v) = args.get(i + 1) {
                    pending.midi = Some(fs::canonicalize(v).unwrap_or(PathBuf::from(v)).to_string_lossy().to_string());
                    i += 1;
                }
            }
            "--sample" | "-s" => {
                if let Some(v) = args.get(i + 1) {
                    pending.sample = Some(fs::canonicalize(v).unwrap_or(PathBuf::from(v)).to_string_lossy().to_string());
                    pending.tab = "crusher".into();
                    i += 1;
                }
            }
            "--bpm" | "-b" => {
                if let Some(v) = args.get(i + 1) {
                    pending.bpm = v.parse().ok();
                    i += 1;
                }
            }
            "--tab" | "-t" => {
                if let Some(v) = args.get(i + 1) {
                    pending.tab = v.clone();
                    i += 1;
                }
            }
            _ => {
                if !a.starts_with('-') {
                    let path = fs::canonicalize(a).unwrap_or(PathBuf::from(a));
                    let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("").to_lowercase();
                    let s = path.to_string_lossy().to_string();
                    match ext.as_str() {
                        "mid" | "midi" => pending.midi = Some(s),
                        "wav" => {
                            pending.sample = Some(s);
                            pending.tab = "crusher".into();
                        }
                        _ => {}
                    }
                }
            }
        }
        i += 1;
    }

    pending
}

#[tauri::command]
fn export_wav(app: tauri::AppHandle, b64data: String, default_name: String) -> Result<String, String> {
    use tauri_plugin_dialog::DialogExt;

    let data = base64::Engine::decode(&base64::engine::general_purpose::STANDARD, &b64data)
        .map_err(|e| e.to_string())?;

    let file_path = app
        .dialog()
        .file()
        .set_file_name(&default_name)
        .add_filter("WAV Audio", &["wav"])
        .blocking_save_file();

    match file_path {
        Some(fp) => {
            let path = fp
                .as_path()
                .ok_or_else(|| "Invalid file path".to_string())?;
            fs::write(path, &data).map_err(|e| e.to_string())?;
            Ok(path.to_string_lossy().to_string())
        }
        None => Ok(String::new()),
    }
}

fn main() {
    let pending = parse_args();

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(pending)
        .invoke_handler(tauri::generate_handler![read_file, save_file, get_pending, export_wav])
        .run(tauri::generate_context!())
        .expect("failed to run app");
}
