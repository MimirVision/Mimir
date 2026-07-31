#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// Rust command layer (settings, sync, feedback/collections listing, CVAT
// links) is built out in a follow-up pass -- this scaffold proves the
// Tauri/React/Vite plumbing end to end with one placeholder command.

#[tauri::command]
fn app_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![app_version])
        .run(tauri::generate_context!())
        .expect("error while running Mimir Forge");
}
