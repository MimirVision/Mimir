#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;

use commands::{
    clear_secret, get_settings, get_status, list_collections, list_feedback, open_in_cvat, run_sync,
    save_secret, save_settings, show_collection, show_feedback,
};

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            get_settings,
            save_settings,
            save_secret,
            clear_secret,
            run_sync,
            get_status,
            list_feedback,
            show_feedback,
            list_collections,
            show_collection,
            open_in_cvat,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Mimir Forge");
}
