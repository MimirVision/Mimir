// Rust command layer for Mimir Forge. Every command here shells out to
// `mimir_training_ground.py` in C:\Mimir_Backend -- same idiom as
// C:\Mimir\src-tauri\src\main.rs's `backend_command`/`resolve_core_v2_*_runtime`
// pattern, simplified because this app only ever runs on the developer's own
// machine (no packaged sidecar fallback needed, unlike the tester-facing app).

use keyring::Entry;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;

const CREATE_NO_WINDOW: u32 = 0x08000000;
const BACKEND_ROOT: &str = r"C:\Mimir_Backend";
const BACKEND_PYTHON: &str = r"C:\Mimir_Backend\.venv\Scripts\python.exe";
const BACKEND_SCRIPT: &str = r"C:\Mimir_Backend\mimir_training_ground.py";
const KEYRING_SERVICE: &str = "MimirForge";
const SETTINGS_FILE: &str = "settings.json";

#[derive(Serialize)]
pub struct ForgeError {
    message: String,
    stdout: String,
    stderr: String,
}

impl ForgeError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            stdout: String::new(),
            stderr: String::new(),
        }
    }

    fn with_output(message: impl Into<String>, stdout: String, stderr: String) -> Self {
        Self {
            message: message.into(),
            stdout,
            stderr,
        }
    }
}

// --- Settings -----------------------------------------------------------

#[derive(Serialize, Deserialize, Clone, Default)]
pub struct Settings {
    #[serde(default)]
    pub dataset_root: String,
    #[serde(default)]
    pub inbox: String,
    #[serde(default)]
    pub feedback_inbox: String,
    #[serde(default)]
    pub identity_path: String,
    #[serde(default)]
    pub r2_endpoint: String,
    #[serde(default)]
    pub r2_bucket: String,
    #[serde(default)]
    pub cvat_url: String,
    #[serde(default)]
    pub create_cvat_tasks: bool,
}

#[derive(Serialize)]
pub struct SettingsView {
    #[serde(flatten)]
    pub settings: Settings,
    pub has_r2_access_key_id: bool,
    pub has_r2_secret_access_key: bool,
    pub has_cvat_token: bool,
}

#[derive(Serialize, Deserialize, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SecretField {
    R2AccessKeyId,
    R2SecretAccessKey,
    CvatToken,
}

impl SecretField {
    fn keyring_username(self) -> &'static str {
        match self {
            SecretField::R2AccessKeyId => "r2_access_key_id",
            SecretField::R2SecretAccessKey => "r2_secret_access_key",
            SecretField::CvatToken => "cvat_token",
        }
    }
}

fn secret_entry(field: SecretField) -> Result<Entry, ForgeError> {
    Entry::new(KEYRING_SERVICE, field.keyring_username())
        .map_err(|error| ForgeError::new(format!("Could not reach Windows Credential Manager: {error}")))
}

fn read_secret(field: SecretField) -> Result<Option<String>, ForgeError> {
    match secret_entry(field)?.get_password() {
        Ok(value) => Ok(Some(value)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(error) => Err(ForgeError::new(format!(
            "Could not read the stored credential: {error}"
        ))),
    }
}

fn settings_path(app: &tauri::AppHandle) -> Result<PathBuf, ForgeError> {
    let dir = tauri::Manager::path(app)
        .app_data_dir()
        .map_err(|error| ForgeError::new(format!("Could not resolve the app data folder: {error}")))?;
    Ok(dir.join(SETTINGS_FILE))
}

fn read_settings(app: &tauri::AppHandle) -> Result<Settings, ForgeError> {
    let path = settings_path(app)?;
    match std::fs::read_to_string(&path) {
        Ok(text) => serde_json::from_str(&text)
            .map_err(|error| ForgeError::new(format!("settings.json is not valid: {error}"))),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(Settings::default()),
        Err(error) => Err(ForgeError::new(format!("Could not read settings.json: {error}"))),
    }
}

fn write_settings(app: &tauri::AppHandle, settings: &Settings) -> Result<(), ForgeError> {
    let path = settings_path(app)?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|error| ForgeError::new(format!("Could not create the app data folder: {error}")))?;
    }
    let body = serde_json::to_string_pretty(settings)
        .map_err(|error| ForgeError::new(format!("Could not serialize settings: {error}")))?;
    // Write-to-temp-then-rename so a crash mid-write can never leave a
    // truncated settings.json -- same atomic-write idiom as C:\Mimir's
    // write_json_atomically.
    let temp_path = path.with_extension("json.tmp");
    std::fs::write(&temp_path, body)
        .map_err(|error| ForgeError::new(format!("Could not write settings.json: {error}")))?;
    std::fs::rename(&temp_path, &path)
        .map_err(|error| ForgeError::new(format!("Could not save settings.json: {error}")))?;
    Ok(())
}

#[tauri::command]
pub fn get_settings(app: tauri::AppHandle) -> Result<SettingsView, ForgeError> {
    let settings = read_settings(&app)?;
    Ok(SettingsView {
        has_r2_access_key_id: read_secret(SecretField::R2AccessKeyId)?.is_some(),
        has_r2_secret_access_key: read_secret(SecretField::R2SecretAccessKey)?.is_some(),
        has_cvat_token: read_secret(SecretField::CvatToken)?.is_some(),
        settings,
    })
}

#[tauri::command]
pub fn save_settings(app: tauri::AppHandle, settings: Settings) -> Result<(), ForgeError> {
    write_settings(&app, &settings)
}

#[tauri::command]
pub fn save_secret(field: SecretField, value: String) -> Result<(), ForgeError> {
    if value.trim().is_empty() {
        return Err(ForgeError::new("That credential can't be blank."));
    }
    secret_entry(field)?
        .set_password(&value)
        .map_err(|error| ForgeError::new(format!("Could not store the credential: {error}")))
}

#[tauri::command]
pub fn clear_secret(field: SecretField) -> Result<(), ForgeError> {
    match secret_entry(field)?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(error) => Err(ForgeError::new(format!("Could not clear the credential: {error}"))),
    }
}

// --- Backend process plumbing --------------------------------------------

#[derive(Debug)]
struct BackendOutput {
    stdout: String,
    stderr: String,
    exit_code: Option<i32>,
}

fn run_backend(args: &[&str], extra_env: &[(&str, String)]) -> Result<BackendOutput, ForgeError> {
    if !std::path::Path::new(BACKEND_PYTHON).exists() {
        return Err(ForgeError::new(format!(
            "Python venv not found at {BACKEND_PYTHON}. Set it up in {BACKEND_ROOT} first."
        )));
    }

    let mut command = Command::new(BACKEND_PYTHON);
    command
        .arg(BACKEND_SCRIPT)
        .args(args)
        .current_dir(BACKEND_ROOT)
        .creation_flags(CREATE_NO_WINDOW);
    for (key, value) in extra_env {
        command.env(key, value);
    }

    let output = command
        .output()
        .map_err(|error| ForgeError::new(format!("Could not run mimir_training_ground.py: {error}")))?;

    Ok(BackendOutput {
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        exit_code: output.status.code(),
    })
}

fn require_success(output: BackendOutput) -> Result<BackendOutput, ForgeError> {
    if output.exit_code == Some(0) {
        Ok(output)
    } else {
        let reason = output
            .stderr
            .lines()
            .next_back()
            .filter(|line| !line.trim().is_empty())
            .unwrap_or("mimir_training_ground.py exited with an error.")
            .to_string();
        Err(ForgeError::with_output(reason, output.stdout, output.stderr))
    }
}

fn parse_json_stdout(output: &BackendOutput) -> Result<Value, ForgeError> {
    serde_json::from_str(output.stdout.trim()).map_err(|error| {
        ForgeError::with_output(
            format!("Could not parse mimir_training_ground.py's output: {error}"),
            output.stdout.clone(),
            output.stderr.clone(),
        )
    })
}

/// Extracts the payload after a `MARKER: ` prefix on its own line, tolerating
/// interleaved progress output before or after it (used for `sync`, which
/// prints human-readable progress lines and then one final structured line).
fn extract_marker_line(stdout: &str, marker: &str) -> Option<String> {
    stdout
        .lines()
        .rev()
        .find_map(|line| line.strip_prefix(marker).map(str::to_string))
}

fn r2_env(settings: &Settings) -> Result<Vec<(&'static str, String)>, ForgeError> {
    let access_key_id = read_secret(SecretField::R2AccessKeyId)?
        .ok_or_else(|| ForgeError::new("No R2 access key saved yet. Add one in Settings."))?;
    let secret_access_key = read_secret(SecretField::R2SecretAccessKey)?
        .ok_or_else(|| ForgeError::new("No R2 secret key saved yet. Add one in Settings."))?;
    let mut env = vec![
        ("MIMIR_R2_ACCESS_KEY_ID", access_key_id),
        ("MIMIR_R2_SECRET_ACCESS_KEY", secret_access_key),
    ];
    if !settings.r2_endpoint.is_empty() {
        env.push(("MIMIR_R2_ENDPOINT", settings.r2_endpoint.clone()));
    }
    if !settings.r2_bucket.is_empty() {
        env.push(("MIMIR_R2_BUCKET", settings.r2_bucket.clone()));
    }
    Ok(env)
}

// --- Sync -----------------------------------------------------------------

#[derive(Serialize)]
pub struct SyncOutcome {
    progress_log: String,
    result: Value,
}

#[tauri::command]
pub async fn run_sync(app: tauri::AppHandle) -> Result<SyncOutcome, ForgeError> {
    tauri::async_runtime::spawn_blocking(move || {
        let settings = read_settings(&app)?;
        if settings.dataset_root.is_empty() || settings.inbox.is_empty() || settings.feedback_inbox.is_empty()
            || settings.identity_path.is_empty()
        {
            return Err(ForgeError::new(
                "Dataset root, inbox, feedback inbox, and identity file must all be set before syncing.",
            ));
        }

        let env = r2_env(&settings)?;
        let cvat_token = read_secret(SecretField::CvatToken)?.unwrap_or_default();

        let mut args = vec![
            "sync".to_string(),
            "--dataset-root".to_string(),
            settings.dataset_root.clone(),
            "--inbox".to_string(),
            settings.inbox.clone(),
            "--feedback-inbox".to_string(),
            settings.feedback_inbox.clone(),
            "--identity".to_string(),
            settings.identity_path.clone(),
        ];
        if settings.create_cvat_tasks {
            args.push("--create-cvat-tasks".to_string());
            if !settings.cvat_url.is_empty() {
                args.push("--cvat-url".to_string());
                args.push(settings.cvat_url.clone());
            }
            if !cvat_token.is_empty() {
                args.push("--cvat-token".to_string());
                args.push(cvat_token);
            }
        }
        let arg_refs: Vec<&str> = args.iter().map(String::as_str).collect();

        let output = require_success(run_backend(&arg_refs, &env)?)?;
        let marker = extract_marker_line(&output.stdout, "MIMIR_SYNC_RESULT_JSON: ").ok_or_else(|| {
            ForgeError::with_output(
                "Sync finished but did not report a result. Check the log below.",
                output.stdout.clone(),
                output.stderr.clone(),
            )
        })?;
        let result: Value = serde_json::from_str(&marker).map_err(|error| {
            ForgeError::with_output(
                format!("Could not parse the sync result: {error}"),
                output.stdout.clone(),
                output.stderr.clone(),
            )
        })?;

        Ok(SyncOutcome {
            progress_log: output.stdout,
            result,
        })
    })
    .await
    .map_err(|error| ForgeError::new(format!("Sync task panicked: {error}")))?
}

// --- Status / feedback / collections --------------------------------------

#[tauri::command]
pub async fn get_status(app: tauri::AppHandle) -> Result<Value, ForgeError> {
    tauri::async_runtime::spawn_blocking(move || {
        let settings = read_settings(&app)?;
        let output = require_success(run_backend(
            &["status", "--json", "--dataset-root", &settings.dataset_root],
            &[],
        )?)?;
        parse_json_stdout(&output)
    })
    .await
    .map_err(|error| ForgeError::new(format!("Status task panicked: {error}")))?
}

#[tauri::command]
pub async fn list_feedback(app: tauri::AppHandle) -> Result<Value, ForgeError> {
    tauri::async_runtime::spawn_blocking(move || {
        let settings = read_settings(&app)?;
        let output = require_success(run_backend(
            &["feedback", "list", "--json", "--feedback-inbox", &settings.feedback_inbox],
            &[],
        )?)?;
        parse_json_stdout(&output)
    })
    .await
    .map_err(|error| ForgeError::new(format!("Feedback list task panicked: {error}")))?
}

#[tauri::command]
pub async fn show_feedback(app: tauri::AppHandle, package_id: String) -> Result<Value, ForgeError> {
    tauri::async_runtime::spawn_blocking(move || {
        let settings = read_settings(&app)?;
        let output = require_success(run_backend(
            &[
                "feedback",
                "show",
                &package_id,
                "--json",
                "--feedback-inbox",
                &settings.feedback_inbox,
            ],
            &[],
        )?)?;
        parse_json_stdout(&output)
    })
    .await
    .map_err(|error| ForgeError::new(format!("Feedback detail task panicked: {error}")))?
}

#[tauri::command]
pub async fn list_collections(app: tauri::AppHandle) -> Result<Value, ForgeError> {
    tauri::async_runtime::spawn_blocking(move || {
        let settings = read_settings(&app)?;
        let output = require_success(run_backend(
            &["collections", "list", "--json", "--dataset-root", &settings.dataset_root],
            &[],
        )?)?;
        parse_json_stdout(&output)
    })
    .await
    .map_err(|error| ForgeError::new(format!("Collections list task panicked: {error}")))?
}

#[tauri::command]
pub async fn show_collection(app: tauri::AppHandle, package_id: String) -> Result<Value, ForgeError> {
    tauri::async_runtime::spawn_blocking(move || {
        let settings = read_settings(&app)?;
        let cvat_token = read_secret(SecretField::CvatToken)?.unwrap_or_default();

        let mut args = vec![
            "collections".to_string(),
            "show".to_string(),
            package_id,
            "--dataset-root".to_string(),
            settings.dataset_root.clone(),
        ];
        if !settings.cvat_url.is_empty() {
            args.push("--cvat-url".to_string());
            args.push(settings.cvat_url.clone());
            if !cvat_token.is_empty() {
                args.push("--cvat-token".to_string());
                args.push(cvat_token);
            }
        }
        let arg_refs: Vec<&str> = args.iter().map(String::as_str).collect();

        let output = require_success(run_backend(&arg_refs, &[])?)?;
        parse_json_stdout(&output)
    })
    .await
    .map_err(|error| ForgeError::new(format!("Collection detail task panicked: {error}")))?
}

#[tauri::command]
pub async fn open_in_cvat(app: tauri::AppHandle, task_id: i64) -> Result<(), ForgeError> {
    tauri::async_runtime::spawn_blocking(move || {
        let settings = read_settings(&app)?;
        if settings.cvat_url.is_empty() {
            return Err(ForgeError::new("Set a CVAT URL in Settings first."));
        }
        let url = format!("{}/tasks/{}", settings.cvat_url.trim_end_matches('/'), task_id);
        Command::new("explorer")
            .arg(url)
            .spawn()
            .map_err(|error| ForgeError::new(format!("Could not open CVAT: {error}")))?;
        Ok(())
    })
    .await
    .map_err(|error| ForgeError::new(format!("Open-in-CVAT task panicked: {error}")))?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn marker_line_is_found_after_interleaved_progress_output() {
        let stdout = "downloaded contribution: contributions/2026/07/a.age\n\
             Intaking 1 contribution package(s)...\n\
             MIMIR_SYNC_RESULT_JSON: {\"new_contribution_count\": 1}\n";
        let marker = extract_marker_line(stdout, "MIMIR_SYNC_RESULT_JSON: ").expect("marker present");
        let value: Value = serde_json::from_str(&marker).expect("valid json");
        assert_eq!(value["new_contribution_count"], 1);
    }

    #[test]
    fn missing_marker_line_returns_none() {
        assert!(extract_marker_line("No new submissions.\n", "MIMIR_SYNC_RESULT_JSON: ").is_none());
    }

    #[test]
    fn settings_round_trip_through_json() {
        let settings = Settings {
            dataset_root: r"C:\Mimir_Backend\MimirOutputV2\training".to_string(),
            inbox: r"C:\Mimir_Backend\inbox".to_string(),
            feedback_inbox: r"C:\Mimir_Backend\feedback_inbox".to_string(),
            identity_path: r"C:\keys\identity.txt".to_string(),
            r2_endpoint: "https://example.r2.cloudflarestorage.com".to_string(),
            r2_bucket: "mimir-intake".to_string(),
            cvat_url: "http://localhost:8080".to_string(),
            create_cvat_tasks: true,
        };
        let text = serde_json::to_string(&settings).expect("serialize");
        let parsed: Settings = serde_json::from_str(&text).expect("deserialize");
        assert_eq!(parsed.dataset_root, settings.dataset_root);
        assert_eq!(parsed.create_cvat_tasks, settings.create_cvat_tasks);
    }

    #[test]
    fn settings_missing_fields_default_instead_of_failing() {
        let parsed: Settings = serde_json::from_str("{}").expect("empty object still parses");
        assert_eq!(parsed.dataset_root, "");
        assert!(!parsed.create_cvat_tasks);
    }

    #[test]
    fn require_success_surfaces_the_last_nonblank_stderr_line() {
        let output = BackendOutput {
            stdout: String::new(),
            stderr: "Traceback (most recent call last):\n  ...\nValueError: bad thing\n".to_string(),
            exit_code: Some(1),
        };
        let error = require_success(output).unwrap_err();
        assert_eq!(error.message, "ValueError: bad thing");
    }

    #[test]
    fn require_success_passes_through_zero_exit() {
        let output = BackendOutput {
            stdout: "ok".to_string(),
            stderr: String::new(),
            exit_code: Some(0),
        };
        assert!(require_success(output).is_ok());
    }
}
