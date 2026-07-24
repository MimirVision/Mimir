#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use base64::Engine;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashSet,
    env, fs,
    io::{BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{Arc, Mutex},
    thread,
    time::{Duration, UNIX_EPOCH},
};
use std::os::windows::process::CommandExt;
use tauri::{Emitter, Manager};

// Prevents Windows from popping a visible console window for every console-subsystem
// child process this app spawns (the packaged scanner exe, ollama, taskkill). Without
// this, each scan/AI-check/cancel flashes a terminal window because the child is a
// console app and this GUI app has no console of its own to inherit.
const CREATE_NO_WINDOW: u32 = 0x08000000;

#[allow(dead_code)]
const DEV_BACKEND_ROOT: &str = r"C:\Mimir_Backend";
#[allow(dead_code)]
const DEV_BACKEND_PYTHON: &str = r"C:\Mimir_Backend\.venv\Scripts\python.exe";
#[allow(dead_code)]
const DEV_CORE_V2_SCRIPT: &str = r"C:\Mimir_Backend\mimir_core_v2_scan.py";
#[allow(dead_code)]
const DEV_CORE_V2_AI_SCRIPT: &str = r"C:\Mimir_Backend\mimir_core_v2_ai_enrich.py";
#[allow(dead_code)]
const DEV_CORE_V2_ACTION_SCRIPT: &str = r"C:\Mimir_Backend\mimir_core_v2_actions.py";
#[allow(dead_code)]
const DEV_CORE_V2_DATASET_SCRIPT: &str = r"C:\Mimir_Backend\mimir_core_v2_dataset.py";
#[allow(dead_code)]
const DEV_CORE_V2_SCAN_EXE: &str = r"C:\Mimir_Backend\dist_backend\mimir-core-v2-scan.exe";
#[allow(dead_code)]
const DEV_CORE_V2_AI_EXE: &str = r"C:\Mimir_Backend\dist_backend\mimir-core-v2-ai-enrich.exe";
#[allow(dead_code)]
const DEV_CORE_V2_ACTIONS_EXE: &str = r"C:\Mimir_Backend\dist_backend\mimir-core-v2-actions.exe";
#[allow(dead_code)]
const DEV_CORE_V2_DATASET_EXE: &str = r"C:\Mimir_Backend\dist_backend\mimir-core-v2-dataset.exe";
const BACKEND_RESOURCE_FOLDER: &str = "mimir-backend";
const CORE_V2_SCAN_EXE_NAME: &str = "mimir-core-v2-scan.exe";
const CORE_V2_AI_EXE_NAME: &str = "mimir-core-v2-ai-enrich.exe";
const CORE_V2_ACTIONS_EXE_NAME: &str = "mimir-core-v2-actions.exe";
const CORE_V2_DATASET_EXE_NAME: &str = "mimir-core-v2-dataset.exe";
const AGE_EXE_NAME: &str = "age.exe";
const TRAINING_AGE_RECIPIENT: &str = "age1ahsfxe3vh8u86cvrknya8pjg8nhydlw0jxw72h68s886qsp8lu2sxq942n";
const DEFAULT_VISION_MODEL: &str = "qwen2.5vl:7b";
const OLLAMA_DOWNLOAD_URL: &str = "https://ollama.com/download";

#[derive(Serialize)]
struct ScanResult {
    stdout: String,
    stderr: String,
    session_path: String,
    latest_session_path: String,
    latest_session_modified_time: String,
    active_output_dir: String,
    output_argument_used: String,
    backend_mode: String,
    backend_runner: String,
    backend_command: String,
}

#[derive(Serialize)]
struct SessionHistoryEntry {
    session_id: String,
    session_path: String,
    source_name: String,
    source_path: String,
    created_at: String,
    incidents: u64,
    important: u64,
    review: u64,
    ignore: u64,
    modified_time: String,
}

#[derive(Serialize)]
struct ClipActionResult {
    ok: bool,
    action: String,
    incident_id: String,
    message: String,
    updated_session: String,
    stdout: String,
    stderr: String,
}

#[derive(Serialize)]
struct StorageActionResult {
    ok: bool,
    action: String,
    incident_id: String,
    message: String,
    updated_session: String,
    report_json: String,
    backend_runner: String,
    stdout: String,
    stderr: String,
}

#[derive(Serialize)]
struct IncidentFeedbackResult {
    ok: bool,
    feedback_folder: String,
    feedback_file: String,
    video_copied: bool,
    message: String,
}

#[derive(Serialize)]
struct TrainingContributionResult {
    ok: bool,
    output_path: String,
    backend_runner: String,
    backend_command: String,
    message: String,
}

#[derive(Serialize)]
struct ScanFailure {
    message: String,
    stdout: String,
    stderr: String,
}

#[derive(Clone, Default)]
struct ActiveScanProcess {
    pid: Arc<Mutex<Option<u32>>>,
}

struct ActiveScanGuard {
    pid: Arc<Mutex<Option<u32>>>,
}

impl Drop for ActiveScanGuard {
    fn drop(&mut self) {
        if let Ok(mut active) = self.pid.lock() {
            *active = None;
        }
    }
}

#[derive(Serialize)]
struct SystemCheckItem {
    id: String,
    label: String,
    ok: bool,
    message: String,
    why_it_matters: String,
    suggested_fix: String,
    technical_details: String,
}

#[derive(Serialize)]
struct SystemCheckResult {
    ok: bool,
    checked_at: String,
    items: Vec<SystemCheckItem>,
}

#[derive(Clone, Serialize)]
struct ScanProgressLine {
    line: String,
}

#[derive(Clone, Serialize)]
struct TeslaCamDriveEvent {
    drive: String,
    teslacam_path: String,
}

#[derive(Serialize)]
struct LocalAiStatus {
    ok: bool,
    ollama_available: bool,
    model_installed: bool,
    selected_model: String,
    message: String,
    technical_details: String,
}

#[derive(Clone, Serialize)]
struct LocalAiInstallLine {
    line: String,
}

#[derive(Serialize)]
struct LocalAiInstallResult {
    ok: bool,
    selected_model: String,
    stdout: String,
    stderr: String,
    message: String,
}

#[derive(Clone)]
enum BackendMode {
    #[cfg(debug_assertions)]
    CoreV2Python,
    CoreV2Exe,
}

#[derive(Clone)]
struct BackendRuntime {
    executable: PathBuf,
    current_dir: PathBuf,
    session_path: PathBuf,
    mode: BackendMode,
}

impl BackendRuntime {
    fn mode_label(&self) -> &'static str {
        match self.mode {
            #[cfg(debug_assertions)]
            BackendMode::CoreV2Python => "mimir_core_v2",
            BackendMode::CoreV2Exe => "mimir_core_v2_exe",
        }
    }

    fn is_python_fallback(&self) -> bool {
        #[cfg(debug_assertions)]
        {
            return matches!(self.mode, BackendMode::CoreV2Python);
        }

        #[cfg(not(debug_assertions))]
        false
    }

    fn runner_label(&self) -> &'static str {
        match self.mode {
            BackendMode::CoreV2Exe => {
                if self.executable.starts_with(Path::new(DEV_BACKEND_ROOT)) {
                    "dev_exe"
                } else {
                    "sidecar"
                }
            }
            #[cfg(debug_assertions)]
            BackendMode::CoreV2Python => "python_script",
        }
    }

    fn is_core_v2_direct_exe(&self) -> bool {
        matches!(self.mode, BackendMode::CoreV2Exe)
    }
}

impl ScanFailure {
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

fn backend_missing_failure(kind: &str, candidates: &[&str]) -> ScanFailure {
    let details = candidates
        .iter()
        .map(|candidate| {
            let path = Path::new(candidate);
            format!(
                "{}: {}",
                candidate,
                if path.exists() { "found" } else { "missing" }
            )
        })
        .collect::<Vec<_>>()
        .join("\n");

    ScanFailure::with_output(
        "Mimir backend was not found.",
        format!("Could not resolve {} backend command.", kind),
        details,
    )
}

// On Windows, `resource_dir()` resolves to the directory containing the main
// executable, NOT the `resources/` folder inside it -- Tauri's bundler places
// bundled resources under `<exe_dir>/resources/<path-as-declared-in-tauri.conf.json>`,
// so the "resources" segment has to be added back here. Without it, this always
// returns a nonexistent path in a packaged build (dev builds never hit this path,
// since resolve_core_v2_*_runtime short-circuits to C:\Mimir_Backend first).
fn backend_resource_path(resource_dir: &Path, exe_name: &str) -> PathBuf {
    resource_dir.join("resources").join(BACKEND_RESOURCE_FOLDER).join(exe_name)
}

fn resource_core_v2_exe(app: &tauri::AppHandle, exe_name: &str) -> Option<PathBuf> {
    app.path()
        .resource_dir()
        .ok()
        .map(|resource_dir| backend_resource_path(&resource_dir, exe_name))
        .filter(|path| path.exists())
}

fn resource_core_v2_runtime(app: &tauri::AppHandle, exe_name: &str) -> Option<BackendRuntime> {
    let executable = resource_core_v2_exe(app, exe_name)?;
    let current_dir = executable
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from(DEV_BACKEND_ROOT));

    Some(BackendRuntime {
        executable,
        current_dir,
        session_path: configured_output_dir(app).join("latest_session.json"),
        mode: BackendMode::CoreV2Exe,
    })
}

fn configured_output_dir(app: &tauri::AppHandle) -> PathBuf {
    if let Some(configured) = env::var_os("MIMIR_OUTPUT_DIR") {
        let path = PathBuf::from(configured);
        if path.is_absolute() {
            return path;
        }
    }

    #[cfg(debug_assertions)]
    {
        let development_output = PathBuf::from(DEV_BACKEND_ROOT).join("MimirOutputV2");
        if Path::new(DEV_BACKEND_ROOT).exists() {
            return development_output;
        }
    }

    app.path()
        .app_data_dir()
        .ok()
        .unwrap_or_else(|| {
            env::var_os("LOCALAPPDATA")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("."))
                .join("Mimir")
        })
        .join("MimirOutputV2")
}

fn resolve_core_v2_scan_runtime(app: &tauri::AppHandle) -> Result<BackendRuntime, ScanFailure> {
    #[cfg(debug_assertions)]
    {
        let session_path = configured_output_dir(app).join("latest_session.json");
        if Path::new(DEV_BACKEND_PYTHON).exists() && Path::new(DEV_CORE_V2_SCRIPT).exists() {
            return Ok(BackendRuntime {
                executable: PathBuf::from(DEV_BACKEND_PYTHON),
                current_dir: PathBuf::from(DEV_BACKEND_ROOT),
                session_path: session_path.clone(),
                mode: BackendMode::CoreV2Python,
            });
        }
    }

    #[cfg(debug_assertions)]
    {
        let session_path = configured_output_dir(app).join("latest_session.json");
        if Path::new(DEV_CORE_V2_SCAN_EXE).exists() {
            return Ok(BackendRuntime {
                executable: PathBuf::from(DEV_CORE_V2_SCAN_EXE),
                current_dir: PathBuf::from(DEV_BACKEND_ROOT),
                session_path: session_path.clone(),
                mode: BackendMode::CoreV2Exe,
            });
        }
    }

    if let Some(runtime) = resource_core_v2_runtime(app, CORE_V2_SCAN_EXE_NAME) {
        return Ok(runtime);
    }

    Err(backend_missing_failure(
        "scan",
        &[DEV_CORE_V2_SCAN_EXE, DEV_BACKEND_PYTHON, DEV_CORE_V2_SCRIPT],
    ))
}

fn resolve_core_v2_actions_runtime(app: &tauri::AppHandle) -> Result<BackendRuntime, ScanFailure> {
    #[cfg(debug_assertions)]
    {
        let session_path = configured_output_dir(app).join("latest_session.json");
        if Path::new(DEV_CORE_V2_ACTIONS_EXE).exists() {
            return Ok(BackendRuntime {
                executable: PathBuf::from(DEV_CORE_V2_ACTIONS_EXE),
                current_dir: PathBuf::from(DEV_BACKEND_ROOT),
                session_path: session_path.clone(),
                mode: BackendMode::CoreV2Exe,
            });
        }
    }

    if let Some(runtime) = resource_core_v2_runtime(app, CORE_V2_ACTIONS_EXE_NAME) {
        return Ok(runtime);
    }

    Err(backend_missing_failure(
        "storage action",
        &[
            DEV_CORE_V2_ACTIONS_EXE,
            DEV_BACKEND_PYTHON,
            DEV_CORE_V2_ACTION_SCRIPT,
        ],
    ))
}

fn resolve_core_v2_ai_runtime(app: &tauri::AppHandle) -> Result<BackendRuntime, ScanFailure> {
    #[cfg(debug_assertions)]
    {
        let session_path = configured_output_dir(app).join("latest_session.json");
        if Path::new(DEV_BACKEND_PYTHON).exists() && Path::new(DEV_CORE_V2_AI_SCRIPT).exists() {
            return Ok(BackendRuntime {
                executable: PathBuf::from(DEV_BACKEND_PYTHON),
                current_dir: PathBuf::from(DEV_BACKEND_ROOT),
                session_path,
                mode: BackendMode::CoreV2Python,
            });
        }
        if Path::new(DEV_CORE_V2_AI_EXE).exists() {
            return Ok(BackendRuntime {
                executable: PathBuf::from(DEV_CORE_V2_AI_EXE),
                current_dir: PathBuf::from(DEV_BACKEND_ROOT),
                session_path,
                mode: BackendMode::CoreV2Exe,
            });
        }
    }
    if let Some(runtime) = resource_core_v2_runtime(app, CORE_V2_AI_EXE_NAME) {
        return Ok(runtime);
    }
    Err(backend_missing_failure(
        "AI enrichment",
        &[DEV_CORE_V2_AI_EXE, DEV_BACKEND_PYTHON, DEV_CORE_V2_AI_SCRIPT],
    ))
}

fn resolve_core_v2_dataset_runtime(app: &tauri::AppHandle) -> Result<BackendRuntime, ScanFailure> {
    #[cfg(debug_assertions)]
    {
        let session_path = configured_output_dir(app).join("latest_session.json");
        if Path::new(DEV_BACKEND_PYTHON).exists() && Path::new(DEV_CORE_V2_DATASET_SCRIPT).exists() {
            return Ok(BackendRuntime {
                executable: PathBuf::from(DEV_BACKEND_PYTHON),
                current_dir: PathBuf::from(DEV_BACKEND_ROOT),
                session_path,
                mode: BackendMode::CoreV2Python,
            });
        }
        if Path::new(DEV_CORE_V2_DATASET_EXE).exists() {
            return Ok(BackendRuntime {
                executable: PathBuf::from(DEV_CORE_V2_DATASET_EXE),
                current_dir: PathBuf::from(DEV_BACKEND_ROOT),
                session_path,
                mode: BackendMode::CoreV2Exe,
            });
        }
    }
    if let Some(runtime) = resource_core_v2_runtime(app, CORE_V2_DATASET_EXE_NAME) {
        return Ok(runtime);
    }
    Err(backend_missing_failure(
        "training contribution",
        &[DEV_CORE_V2_DATASET_EXE, DEV_BACKEND_PYTHON, DEV_CORE_V2_DATASET_SCRIPT],
    ))
}

fn age_executable_for_runtime(app: &tauri::AppHandle, runtime: &BackendRuntime) -> Option<PathBuf> {
    if let Some(parent) = runtime.executable.parent() {
        let adjacent = parent.join(AGE_EXE_NAME);
        if adjacent.exists() {
            return Some(adjacent);
        }
    }
    app.path()
        .resource_dir()
        .ok()
        .map(|root| backend_resource_path(&root, AGE_EXE_NAME))
        .filter(|path| path.exists())
}

fn resolve_backend_runtime(app: &tauri::AppHandle) -> Result<BackendRuntime, ScanFailure> {
    resolve_core_v2_scan_runtime(app)
}

fn backend_command(runtime: &BackendRuntime) -> Command {
    let mut command = Command::new(&runtime.executable);
    command.current_dir(&runtime.current_dir);
    command.creation_flags(CREATE_NO_WINDOW);
    command
}

fn active_output_dir(runtime: &BackendRuntime) -> PathBuf {
    runtime
        .session_path
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from(DEV_BACKEND_ROOT).join("MimirOutputV2"))
}

fn should_pass_explicit_output_dir(_runtime: &BackendRuntime) -> bool {
    true
}

fn output_argument_name(runtime: &BackendRuntime) -> &'static str {
    if should_pass_explicit_output_dir(runtime) {
        "--output"
    } else {
        ""
    }
}

fn append_output_dir_arg(command: &mut Command, runtime: &BackendRuntime, output_dir: &Path) {
    if should_pass_explicit_output_dir(runtime) {
        command.arg(output_argument_name(runtime)).arg(output_dir);
    }
}

fn quote_command_part(value: &str) -> String {
    if value.contains(char::is_whitespace) || value.contains('"') {
        format!("\"{}\"", value.replace('"', "\\\""))
    } else {
        value.to_string()
    }
}

fn command_preview(command: &Command) -> String {
    let mut parts = vec![quote_command_part(&command.get_program().to_string_lossy())];
    parts.extend(
        command
            .get_args()
            .map(|arg| quote_command_part(&arg.to_string_lossy())),
    );
    parts.join(" ")
}

fn file_modified_time(path: &Path) -> String {
    fs::metadata(path)
        .and_then(|metadata| metadata.modified())
        .ok()
        .and_then(|modified| modified.duration_since(UNIX_EPOCH).ok())
        .map(|duration| format!("unix:{}", duration.as_secs()))
        .unwrap_or_default()
}

fn validated_session_path(
    app: &tauri::AppHandle,
    requested: Option<String>,
) -> Result<PathBuf, ScanFailure> {
    let output_dir = configured_output_dir(app);
    let candidate = requested
        .filter(|value| !value.trim().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| output_dir.join("latest_session.json"));
    if !candidate.exists() || !candidate.is_file() {
        return Err(ScanFailure::new(
            "The requested Mimir session does not exist.",
        ));
    }
    let canonical_output = output_dir
        .canonicalize()
        .map_err(|error| ScanFailure::new(error.to_string()))?;
    let canonical_candidate = candidate
        .canonicalize()
        .map_err(|error| ScanFailure::new(error.to_string()))?;
    if !canonical_candidate.starts_with(&canonical_output) {
        return Err(ScanFailure::new(
            "The requested session is outside Mimir's output folder.",
        ));
    }
    let filename = canonical_candidate
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or_default();
    if filename != "latest_session.json" && filename != "session.json" {
        return Err(ScanFailure::new(
            "The requested file is not a Mimir session.",
        ));
    }
    Ok(canonical_candidate)
}

fn history_entry(path: &Path) -> Option<SessionHistoryEntry> {
    let contents = fs::read_to_string(path).ok()?;
    let session: Value = serde_json::from_str(&contents).ok()?;
    let summary = session.get("scan_summary").and_then(Value::as_object);
    let source_name = summary
        .and_then(|value| value.get("source_name"))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let source_path = summary
        .and_then(|value| value.get("source_path"))
        .and_then(Value::as_str)
        .or_else(|| session.get("selected_input").and_then(Value::as_str))
        .unwrap_or_default()
        .to_string();
    Some(SessionHistoryEntry {
        session_id: session
            .get("session_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        session_path: path.to_string_lossy().to_string(),
        source_name,
        source_path,
        created_at: session
            .get("session_created_at")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        incidents: session
            .get("incidents_count")
            .and_then(Value::as_u64)
            .unwrap_or(0),
        important: session
            .get("important")
            .and_then(Value::as_u64)
            .unwrap_or(0),
        review: session.get("review").and_then(Value::as_u64).unwrap_or(0),
        ignore: session.get("ignore").and_then(Value::as_u64).unwrap_or(0),
        modified_time: file_modified_time(path),
    })
}

fn list_session_history_sync(
    app: &tauri::AppHandle,
) -> Result<Vec<SessionHistoryEntry>, ScanFailure> {
    let sessions_dir = configured_output_dir(app).join("sessions");
    if !sessions_dir.exists() {
        return Ok(Vec::new());
    }
    let mut entries = Vec::new();
    for directory in
        fs::read_dir(&sessions_dir).map_err(|error| ScanFailure::new(error.to_string()))?
    {
        let directory = directory.map_err(|error| ScanFailure::new(error.to_string()))?;
        let session_path = directory.path().join("session.json");
        if let Some(entry) = history_entry(&session_path) {
            entries.push(entry);
        }
    }
    entries.sort_by(|left, right| right.created_at.cmp(&left.created_at));
    entries.truncate(25);
    Ok(entries)
}

fn append_backend_scan_args(
    command: &mut Command,
    runtime: &BackendRuntime,
    input_folder: &str,
    scan_mode: &str,
    output_dir: &Path,
    use_enhanced_ai: bool,
    vision_model: &str,
    ai_review_budget: Option<u32>,
    ai_timeout_sec: Option<u32>,
) {
    let backend_scan_mode = if scan_mode == "quality" {
        "thorough"
    } else {
        scan_mode
    };

    if runtime.is_core_v2_direct_exe() {
        command
            .arg("--input")
            .arg(input_folder)
            .arg("--mode")
            .arg(backend_scan_mode);
        append_output_dir_arg(command, runtime, output_dir);

        append_experimental_ai_scan_args(
            command,
            use_enhanced_ai,
            vision_model,
            ai_review_budget,
            ai_timeout_sec,
        );
    } else if runtime.is_python_fallback() {
        command
            .arg(DEV_CORE_V2_SCRIPT)
            .arg("--input")
            .arg(input_folder)
            .arg("--mode")
            .arg(backend_scan_mode);
        append_output_dir_arg(command, runtime, output_dir);

        append_experimental_ai_scan_args(
            command,
            use_enhanced_ai,
            vision_model,
            ai_review_budget,
            ai_timeout_sec,
        );
    } else {
        command
            .arg("scan")
            .arg("--input")
            .arg(input_folder)
            .arg("--mode")
            .arg(backend_scan_mode);
        append_output_dir_arg(command, runtime, output_dir);

        append_experimental_ai_scan_args(
            command,
            use_enhanced_ai,
            vision_model,
            ai_review_budget,
            ai_timeout_sec,
        );
    }
}

fn append_experimental_ai_scan_args(
    command: &mut Command,
    use_enhanced_ai: bool,
    vision_model: &str,
    ai_review_budget: Option<u32>,
    ai_timeout_sec: Option<u32>,
) {
    if !use_enhanced_ai {
        return;
    }

    command.arg("--vlm").arg(vision_model);
    command
        .arg("--ai-review-budget")
        .arg(ai_review_budget.unwrap_or(5).to_string())
        .arg("--ai-timeout-sec")
        .arg(ai_timeout_sec.unwrap_or(60).to_string())
        .arg("--defer-ai");
}

fn valid_scan_mode(value: &str) -> bool {
    matches!(value, "fast" | "balanced" | "quality" | "thorough")
}

fn valid_status(value: &str) -> bool {
    matches!(value, "IGNORE" | "REVIEW" | "IMPORTANT")
}

fn open_folder_with_explorer(path: &Path) -> Result<(), ScanFailure> {
    Command::new("explorer")
        .arg(path)
        .spawn()
        .map_err(|error| ScanFailure::new(error.to_string()))?;

    Ok(())
}

fn run_ollama_list() -> Result<std::process::Output, std::io::Error> {
    Command::new("ollama")
        .arg("list")
        .creation_flags(CREATE_NO_WINDOW)
        .output()
}

fn local_ai_status_sync(selected_model: Option<String>) -> LocalAiStatus {
    let selected_model = selected_model
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_VISION_MODEL.to_string());

    match run_ollama_list() {
        Ok(output) => {
            let details = command_details(&output.stdout, &output.stderr);

            if !output.status.success() {
                return LocalAiStatus {
                    ok: false,
                    ollama_available: false,
                    model_installed: false,
                    selected_model,
                    message: "Local AI engine is not running or is not installed.".to_string(),
                    technical_details: details,
                };
            }

            let stdout = String::from_utf8_lossy(&output.stdout).to_lowercase();
            let model_installed = stdout.contains(&selected_model.to_lowercase());

            LocalAiStatus {
                ok: model_installed,
                ollama_available: true,
                model_installed,
                selected_model: selected_model.clone(),
                message: if model_installed {
                    "Local AI ready.".to_string()
                } else {
                    "Local vision model is missing.".to_string()
                },
                technical_details: details,
            }
        }
        Err(error) => LocalAiStatus {
            ok: false,
            ollama_available: false,
            model_installed: false,
            selected_model,
            message: "Local AI engine is missing.".to_string(),
            technical_details: error.to_string(),
        },
    }
}

fn emit_install_stream<R: Read + Send + 'static>(
    mut reader: R,
    window: tauri::WebviewWindow,
    output: std::sync::Arc<std::sync::Mutex<String>>,
) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        let mut buffer = [0_u8; 1];
        let mut line = String::new();

        loop {
            match reader.read(&mut buffer) {
                Ok(0) => break,
                Ok(_) => {
                    let character = buffer[0] as char;

                    if character == '\n' || character == '\r' {
                        let trimmed = line.trim();

                        if !trimmed.is_empty() {
                            if let Ok(mut output_text) = output.lock() {
                                output_text.push_str(trimmed);
                                output_text.push('\n');
                            }

                            let _ = window.emit(
                                "local-ai-install-output",
                                LocalAiInstallLine {
                                    line: trimmed.to_string(),
                                },
                            );
                        }

                        line.clear();
                    } else {
                        line.push(character);
                    }
                }
                Err(_) => break,
            }
        }

        let trimmed = line.trim();
        if !trimmed.is_empty() {
            if let Ok(mut output_text) = output.lock() {
                output_text.push_str(trimmed);
                output_text.push('\n');
            }

            let _ = window.emit(
                "local-ai-install-output",
                LocalAiInstallLine {
                    line: trimmed.to_string(),
                },
            );
        }
    })
}

fn pull_local_ai_model_sync(
    window: tauri::WebviewWindow,
    selected_model: Option<String>,
) -> Result<LocalAiInstallResult, ScanFailure> {
    let selected_model = selected_model
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_VISION_MODEL.to_string());

    let mut child = Command::new("ollama")
        .arg("pull")
        .arg(&selected_model)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .map_err(|error| {
            ScanFailure::new(format!(
        "Local AI engine could not start. Install it, then return to Mimir and click Recheck. {}",
        error
      ))
        })?;

    let stdout_pipe = child
        .stdout
        .take()
        .ok_or_else(|| ScanFailure::new("Could not read local AI download output."))?;
    let stderr_pipe = child
        .stderr
        .take()
        .ok_or_else(|| ScanFailure::new("Could not read local AI download errors."))?;

    let stdout = std::sync::Arc::new(std::sync::Mutex::new(String::new()));
    let stderr = std::sync::Arc::new(std::sync::Mutex::new(String::new()));

    let stdout_handle = emit_install_stream(stdout_pipe, window.clone(), stdout.clone());
    let stderr_handle = emit_install_stream(stderr_pipe, window, stderr.clone());

    let status = child
        .wait()
        .map_err(|error| ScanFailure::new(error.to_string()))?;
    let _ = stdout_handle.join();
    let _ = stderr_handle.join();

    let stdout_text = stdout.lock().map(|value| value.clone()).unwrap_or_default();
    let stderr_text = stderr.lock().map(|value| value.clone()).unwrap_or_default();

    if !status.success() {
        return Ok(LocalAiInstallResult {
            ok: false,
            selected_model,
            stdout: stdout_text,
            stderr: stderr_text,
            message: "Local vision model download failed.".to_string(),
        });
    }

    Ok(LocalAiInstallResult {
        ok: true,
        selected_model,
        stdout: stdout_text,
        stderr: stderr_text,
        message: "Local AI ready.".to_string(),
    })
}

fn default_mimir_library_root() -> Result<PathBuf, ScanFailure> {
    let home = std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
        .ok_or_else(|| ScanFailure::new("Could not resolve the user profile folder."))?;

    Ok(home.join("Videos").join("Mimir Library"))
}

fn default_documents_root() -> Result<PathBuf, ScanFailure> {
    let home = std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
        .ok_or_else(|| ScanFailure::new("Could not resolve the user profile folder."))?;

    Ok(home.join("Documents"))
}

fn append_app_crash_log_sync(
    incident_id: String,
    attempted_video_path: String,
    error_message: String,
    stack_trace: String,
) -> Result<(), ScanFailure> {
    let log_dir = default_documents_root()?.join("Mimir Logs");
    fs::create_dir_all(&log_dir).map_err(|error| ScanFailure::new(error.to_string()))?;
    let log_path = log_dir.join("app_crash_log.txt");
    let mut file = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|error| ScanFailure::new(error.to_string()))?;

    writeln!(file, "timestamp: {}", chrono_like_now())
        .map_err(|error| ScanFailure::new(error.to_string()))?;
    writeln!(file, "incident id: {}", incident_id)
        .map_err(|error| ScanFailure::new(error.to_string()))?;
    writeln!(file, "attempted video path: {}", attempted_video_path)
        .map_err(|error| ScanFailure::new(error.to_string()))?;
    writeln!(file, "error message: {}", error_message)
        .map_err(|error| ScanFailure::new(error.to_string()))?;
    writeln!(file, "stack trace: {}", stack_trace)
        .map_err(|error| ScanFailure::new(error.to_string()))?;
    writeln!(file, "---").map_err(|error| ScanFailure::new(error.to_string()))?;

    Ok(())
}

fn safe_filename(value: &str) -> String {
    let mut output = String::new();

    for character in value.chars() {
        if character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.') {
            output.push(character);
        } else {
            output.push('_');
        }
    }

    let trimmed = output.trim_matches('_');
    if trimmed.is_empty() {
        "incident".to_string()
    } else {
        trimmed.chars().take(80).collect()
    }
}

fn save_incident_feedback_sync(
    mut feedback: Value,
    include_video: bool,
    video_path: Option<String>,
) -> Result<IncidentFeedbackResult, ScanFailure> {
    let feedback_root = default_documents_root()?.join("Mimir Feedback");
    fs::create_dir_all(&feedback_root).map_err(|error| ScanFailure::new(error.to_string()))?;

    let incident_id = feedback
        .get("incident_id")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .unwrap_or("incident");
    let submitted_at = chrono_like_now();
    let folder_name = format!(
        "{}_{}",
        safe_filename(incident_id),
        safe_filename(&submitted_at)
    );
    let feedback_folder = feedback_root.join(folder_name);
    fs::create_dir_all(&feedback_folder).map_err(|error| ScanFailure::new(error.to_string()))?;

    if let Some(object) = feedback.as_object_mut() {
        object.insert("saved_at".to_string(), json!(submitted_at));
        object.insert(
            "feedback_folder".to_string(),
            json!(feedback_folder.to_string_lossy().to_string()),
        );
        object.insert("automatic_upload".to_string(), json!(false));
    }

    let mut video_copied = false;

    if include_video {
        let video_copy_result = (|| -> Result<PathBuf, String> {
            let source = video_path
                .as_deref()
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(PathBuf::from)
                .ok_or_else(|| "Video path is not available for this incident.".to_string())?;

            if !source.exists() || !source.is_file() {
                return Err("Video file was not found.".to_string());
            }

            let file_name = source
                .file_name()
                .and_then(|name| name.to_str())
                .map(safe_filename)
                .unwrap_or_else(|| "incident_video.mp4".to_string());
            let destination = feedback_folder.join(file_name);
            fs::copy(&source, &destination).map_err(|error| error.to_string())?;

            if !destination.exists()
                || destination.metadata().map(|meta| meta.len()).unwrap_or(0) == 0
            {
                return Err("Video copy verification failed.".to_string());
            }

            Ok(destination)
        })();

        if let Some(object) = feedback.as_object_mut() {
            match video_copy_result {
                Ok(destination) => {
                    video_copied = true;
                    object.insert(
                        "included_video_path".to_string(),
                        json!(destination.to_string_lossy().to_string()),
                    );
                }
                Err(error) => {
                    object.insert("video_copy_error".to_string(), json!(error));
                }
            }
        }
    }

    let feedback_file = feedback_folder.join("feedback.json");
    write_json_atomically(&feedback_file, &feedback)?;

    Ok(IncidentFeedbackResult {
        ok: true,
        feedback_folder: feedback_folder.to_string_lossy().to_string(),
        feedback_file: feedback_file.to_string_lossy().to_string(),
        video_copied,
        message: "Feedback saved locally. No upload was performed.".to_string(),
    })
}

fn check_item(
    id: &str,
    label: &str,
    ok: bool,
    message: impl Into<String>,
    why_it_matters: &str,
    suggested_fix: &str,
    technical_details: impl Into<String>,
) -> SystemCheckItem {
    SystemCheckItem {
        id: id.to_string(),
        label: label.to_string(),
        ok,
        message: message.into(),
        why_it_matters: why_it_matters.to_string(),
        suggested_fix: suggested_fix.to_string(),
        technical_details: technical_details.into(),
    }
}

fn command_details(stdout: &[u8], stderr: &[u8]) -> String {
    let stdout_text = String::from_utf8_lossy(stdout).trim().to_string();
    let stderr_text = String::from_utf8_lossy(stderr).trim().to_string();

    match (stdout_text.is_empty(), stderr_text.is_empty()) {
        (true, true) => String::new(),
        (false, true) => format!("stdout:\n{}", stdout_text),
        (true, false) => format!("stderr:\n{}", stderr_text),
        (false, false) => format!("stdout:\n{}\n\nstderr:\n{}", stdout_text, stderr_text),
    }
}

fn path_check_details(candidates: &[&str]) -> String {
    candidates
        .iter()
        .map(|candidate| {
            let path = Path::new(candidate);
            format!(
                "{}: {}",
                candidate,
                if path.exists() { "found" } else { "missing" }
            )
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn runtime_details(runtime: &BackendRuntime, candidates: &[&str]) -> String {
    [
        format!("backend_runner: {}", runtime.runner_label()),
        format!("resolved_command: {}", runtime.executable.to_string_lossy()),
        format!(
            "working_directory: {}",
            runtime.current_dir.to_string_lossy()
        ),
        path_check_details(candidates),
    ]
    .into_iter()
    .filter(|value| !value.trim().is_empty())
    .collect::<Vec<_>>()
    .join("\n")
}

fn folder_check_item(
    id: &str,
    label: &str,
    folder: Result<PathBuf, ScanFailure>,
    ready_message: &str,
    missing_message: &str,
) -> SystemCheckItem {
    match folder {
        Ok(path) => {
            let create_result = fs::create_dir_all(&path);
            let ok = create_result.is_ok() && path.is_dir();
            let details = match create_result {
                Ok(_) => format!("folder: {}\nstatus: accessible", path.to_string_lossy()),
                Err(error) => format!("folder: {}\nerror: {}", path.to_string_lossy(), error),
            };

            check_item(
                id,
                label,
                ok,
                if ok { ready_message } else { missing_message },
                "Mimir needs this folder to save scan results and reviewed clips.",
                "Check folder permissions, then click Recheck.",
                details,
            )
        }
        Err(error) => check_item(
            id,
            label,
            false,
            missing_message,
            "Mimir needs this folder to save scan results and reviewed clips.",
            "Check folder permissions, then click Recheck.",
            error.message,
        ),
    }
}

fn run_system_check_sync(app: tauri::AppHandle) -> SystemCheckResult {
    let mut items = Vec::new();

    let scanner_candidates = vec![DEV_CORE_V2_SCAN_EXE, DEV_BACKEND_PYTHON, DEV_CORE_V2_SCRIPT];
    let scanner_runtime = resolve_backend_runtime(&app);

    if let Ok(runtime) = &scanner_runtime {
        if runtime.is_core_v2_direct_exe() || runtime.is_python_fallback() {
            items.push(check_item(
                "local_scanner",
                "Local scanner",
                true,
                "Mimir is ready to scan.",
                "The scanner is required before Mimir can analyze footage.",
                "Try reinstalling Mimir. Technical details are available below.",
                runtime_details(runtime, &scanner_candidates),
            ));
        } else {
            let mut command = backend_command(runtime);
            command.arg("health");

            match command.output() {
                Ok(output) => {
                    let ok = output.status.success();
                    let details = command_details(&output.stdout, &output.stderr);

                    items.push(check_item(
                        "local_scanner",
                        "Local scanner",
                        ok,
                        if ok {
                            "Mimir is ready to scan.".to_string()
                        } else {
                            "Mimir could not start the local scanner.".to_string()
                        },
                        "The scanner is required before Mimir can analyze footage.",
                        "Try reinstalling Mimir. Technical details are available below.",
                        [
                            runtime_details(runtime, &scanner_candidates),
                            details.clone(),
                        ]
                        .join("\n\n"),
                    ));

                    let details_lower = details.to_lowercase();
                    let enhanced_ready = details_lower.contains("\"enhanced_ai_available\": true");

                    items.push(check_item(
                        "enhanced_ai_review",
                        "Enhanced AI review",
                        enhanced_ready,
                        if enhanced_ready {
                            "Enhanced AI review ready.".to_string()
                        } else {
                            "Enhanced AI review is not set up.".to_string()
                        },
                        "Local AI setup is checked separately from the scanner.",
                        "Use Repair setup or Recheck if AI review is required.",
                        details,
                    ));
                }
                Err(error) => items.push(check_item(
                    "local_scanner",
                    "Local scanner",
                    false,
                    "Mimir could not start the local scanner.",
                    "The scanner is required before Mimir can analyze footage.",
                    "Try reinstalling Mimir. Technical details are available below.",
                    [
                        runtime_details(runtime, &scanner_candidates),
                        error.to_string(),
                    ]
                    .join("\n\n"),
                )),
            }
        }
    } else if let Err(error) = scanner_runtime {
        let technical_details = [
            error.message.clone(),
            error.stdout.clone(),
            error.stderr.clone(),
        ]
        .into_iter()
        .filter(|value| !value.trim().is_empty())
        .collect::<Vec<_>>()
        .join("\n\n");

        items.push(check_item(
            "local_scanner",
            "Local scanner",
            false,
            "Mimir cannot find the local scanner.",
            "The scanner is required before Mimir can analyze footage.",
            "Try reinstalling Mimir. Technical details are available below.",
            technical_details,
        ));
    }

    match resolve_core_v2_actions_runtime(&app) {
            Ok(runtime) => items.push(check_item(
                "local_actions",
                "Storage actions",
                true,
                "Mimir storage actions are ready.",
                "These actions move reviewed clips only when you choose a storage action.",
                "Restore the packaged action executable or development action script, then click Recheck.",
                runtime_details(
                    &runtime,
                    &[
                        DEV_CORE_V2_ACTIONS_EXE,
                        DEV_BACKEND_PYTHON,
                        DEV_CORE_V2_ACTION_SCRIPT,
                    ],
                ),
            )),
            Err(error) => items.push(check_item(
                "local_actions",
                "Storage actions",
                false,
                "Mimir cannot find the local storage actions.",
                "Storage actions are required for Move to Library and Move to Mimir Trash.",
                "Restore the packaged action executable or development action script, then click Recheck.",
                [
                    error.message,
                    error.stdout,
                    error.stderr,
                    path_check_details(&[
                        DEV_CORE_V2_ACTIONS_EXE,
                        DEV_BACKEND_PYTHON,
                        DEV_CORE_V2_ACTION_SCRIPT,
                    ]),
                ]
                .into_iter()
                .filter(|value| !value.trim().is_empty())
                .collect::<Vec<_>>()
                .join("\n\n"),
            )),
    }

    let output_folder = Ok(configured_output_dir(&app));
    items.push(folder_check_item(
        "core_v2_output_folder",
        "Scan output folder",
        output_folder,
        "Mimir scan output is ready.",
        "Mimir scan output is not ready.",
    ));

    items.push(folder_check_item(
        "mimir_library_folder",
        "Mimir Library",
        default_mimir_library_root(),
        "Mimir Library is ready.",
        "Mimir Library is not ready.",
    ));

    let ok = items
        .iter()
        .filter(|item| item.id != "enhanced_ai_review")
        .all(|item| item.ok);

    SystemCheckResult {
        ok,
        checked_at: chrono_like_now(),
        items,
    }
}

fn incident_matches(incident: &Value, incident_id: &str, index: usize) -> bool {
    let candidates = [
        incident.get("id"),
        incident.get("incident_id"),
        incident.get("event_id"),
    ];

    if candidates.iter().any(|candidate| {
        candidate
            .and_then(|value| {
                if let Some(text) = value.as_str() {
                    Some(text.to_string())
                } else if value.is_number() {
                    Some(value.to_string())
                } else {
                    None
                }
            })
            .map(|value| value == incident_id)
            .unwrap_or(false)
    }) {
        return true;
    }

    incident_id == index.to_string() || incident_id == (index + 1).to_string()
}

fn write_json_atomically(path: &Path, value: &Value) -> Result<(), ScanFailure> {
    let parent = path
        .parent()
        .ok_or_else(|| ScanFailure::new("JSON path has no parent folder."))?;
    fs::create_dir_all(parent).map_err(|error| ScanFailure::new(error.to_string()))?;

    let temp_path = parent.join(format!(
        ".{}.tmp",
        path.file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("mimir-session")
    ));
    let rendered =
        serde_json::to_string_pretty(value).map_err(|error| ScanFailure::new(error.to_string()))?;
    let mut file =
        fs::File::create(&temp_path).map_err(|error| ScanFailure::new(error.to_string()))?;
    file.write_all(rendered.as_bytes())
        .map_err(|error| ScanFailure::new(error.to_string()))?;
    file.write_all(b"\n")
        .map_err(|error| ScanFailure::new(error.to_string()))?;
    drop(file);
    if path.exists() {
        fs::remove_file(path).map_err(|error| ScanFailure::new(error.to_string()))?;
    }
    fs::rename(&temp_path, path).map_err(|error| ScanFailure::new(error.to_string()))?;
    Ok(())
}

fn incident_json_path(incident: &Value) -> Option<PathBuf> {
    [
        "contact_sheet",
        "hero_thumbnail",
        "thumbnail",
        "best_frame_image",
        "start_frame_image",
        "end_frame_image",
    ]
    .iter()
    .find_map(|field| {
        incident
            .get(*field)
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty())
            .map(|value| {
                PathBuf::from(value)
                    .parent()
                    .map(|parent| parent.join("incident.json"))
            })
            .flatten()
    })
}

fn emit_ai_enrichment_failure(window: &tauri::WebviewWindow, message: &str) {
    let payload = json!({
        "protocol_version": "mimir_progress_v2",
        "phase": "ai_enrichment",
        "stage": "ai_enrichment_error",
        "message": message,
        "local_results_ready": true,
        "ai_enrichment_status": "failed"
    });
    let _ = window.emit(
        "mimir-progress",
        ScanProgressLine {
            line: format!("MIMIR_PROGRESS {}", payload),
        },
    );
}

// Polls fixed drive letters D:-Z: for a `TeslaCam` folder at the root, matching the
// layout `discover_footage_source.py`/`source_discovery.py` already treat as a
// supported USB source. Kept as a plain existence check rather than a full backend
// scan so polling stays cheap; it only tells the user a drive appeared, it never
// starts a scan on its own.
fn scan_for_teslacam_drives() -> HashSet<String> {
    let mut found = HashSet::new();
    for letter in b'D'..=b'Z' {
        let drive = format!("{}:", letter as char);
        if Path::new(&format!("{}\\TeslaCam", drive)).is_dir() {
            found.insert(drive);
        }
    }
    found
}

fn spawn_teslacam_drive_watcher(window: tauri::WebviewWindow) {
    thread::spawn(move || {
        let mut known_drives: HashSet<String> = HashSet::new();
        loop {
            let current_drives = scan_for_teslacam_drives();

            for drive in current_drives.difference(&known_drives) {
                let _ = window.emit(
                    "mimir-teslacam-drive-detected",
                    TeslaCamDriveEvent {
                        drive: drive.clone(),
                        teslacam_path: format!("{}\\TeslaCam", drive),
                    },
                );
            }

            for drive in known_drives.difference(&current_drives) {
                let _ = window.emit(
                    "mimir-teslacam-drive-removed",
                    TeslaCamDriveEvent {
                        drive: drive.clone(),
                        teslacam_path: format!("{}\\TeslaCam", drive),
                    },
                );
            }

            known_drives = current_drives;
            thread::sleep(Duration::from_secs(3));
        }
    });
}

fn spawn_ai_enrichment(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    session_path: PathBuf,
    vision_model: String,
    ai_review_budget: Option<u32>,
    ai_timeout_sec: Option<u32>,
) {
    thread::spawn(move || {
        let runtime = match resolve_core_v2_ai_runtime(&app) {
            Ok(runtime) => runtime,
            Err(error) => {
                emit_ai_enrichment_failure(&window, &error.message);
                return;
            }
        };
        let mut command = backend_command(&runtime);
        if runtime.is_python_fallback() {
            command.arg(DEV_CORE_V2_AI_SCRIPT);
        }
        command
            .arg("--session")
            .arg(&session_path)
            .arg("--vlm")
            .arg(&vision_model)
            .arg("--ai-review-budget")
            .arg(ai_review_budget.unwrap_or(999).to_string())
            .arg("--ai-timeout-sec")
            .arg(ai_timeout_sec.unwrap_or(60).to_string())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        let mut child = match command.spawn() {
            Ok(child) => child,
            Err(error) => {
                emit_ai_enrichment_failure(
                    &window,
                    &format!("Experimental AI could not start: {}", error),
                );
                return;
            }
        };
        let stderr_pipe = child.stderr.take();
        let stderr_handle = thread::spawn(move || {
            let mut stderr = String::new();
            if let Some(pipe) = stderr_pipe {
                let mut reader = BufReader::new(pipe);
                let _ = reader.read_to_string(&mut stderr);
            }
            stderr
        });
        if let Some(stdout) = child.stdout.take() {
            for line in BufReader::new(stdout).lines().flatten() {
                let progress_line = line.trim_start();
                if progress_line.starts_with("MIMIR_PROGRESS") {
                    let _ = window.emit(
                        "mimir-progress",
                        ScanProgressLine {
                            line: progress_line.to_string(),
                        },
                    );
                }
            }
        }
        let status = child.wait();
        let stderr = stderr_handle.join().unwrap_or_default();
        match status {
            Ok(value) if value.success() => {}
            Ok(value) => emit_ai_enrichment_failure(
                &window,
                &format!("Experimental AI ended with exit code {:?}. {}", value.code(), stderr.trim()),
            ),
            Err(error) => emit_ai_enrichment_failure(
                &window,
                &format!("Experimental AI could not finish: {}", error),
            ),
        }
    });
}

fn run_scan_sync(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    selected_folder: String,
    scan_mode: String,
    use_enhanced_ai: bool,
    vision_model: Option<String>,
    ai_review_budget: Option<u32>,
    ai_timeout_sec: Option<u32>,
    active_scan_pid: Arc<Mutex<Option<u32>>>,
) -> Result<ScanResult, ScanFailure> {
    let source_folder = PathBuf::from(selected_folder);

    if !source_folder.exists() || !source_folder.is_dir() {
        return Err(ScanFailure::new("Selected folder could not be found."));
    }

    if !valid_scan_mode(&scan_mode) {
        return Err(ScanFailure::new("Selected scan mode is not supported."));
    }

    let runtime = resolve_backend_runtime(&app)?;

    let source_canonical = source_folder
        .canonicalize()
        .map_err(|error| ScanFailure::new(error.to_string()))?;

    let input_folder = source_canonical.to_string_lossy().to_string();
    let vision_model = vision_model
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_VISION_MODEL.to_string());
    let output_dir = active_output_dir(&runtime);
    fs::create_dir_all(&output_dir).map_err(|error| ScanFailure::new(error.to_string()))?;
    app.asset_protocol_scope()
        .allow_directory(&source_canonical, true)
        .map_err(|error| ScanFailure::new(error.to_string()))?;
    app.asset_protocol_scope()
        .allow_directory(&output_dir, true)
        .map_err(|error| ScanFailure::new(error.to_string()))?;

    let mut command = backend_command(&runtime);
    append_backend_scan_args(
        &mut command,
        &runtime,
        &input_folder,
        &scan_mode,
        &output_dir,
        use_enhanced_ai,
        &vision_model,
        ai_review_budget,
        ai_timeout_sec,
    );
    let backend_command = command_preview(&command);

    {
        let active = active_scan_pid
            .lock()
            .map_err(|_| ScanFailure::new("Could not access scan process state."))?;
        if active.is_some() {
            return Err(ScanFailure::new("A Mimir scan is already running."));
        }
    }

    let mut child = command
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| ScanFailure::new(format!("Bundled backend could not start. {}", error)))?;
    {
        let mut active = active_scan_pid
            .lock()
            .map_err(|_| ScanFailure::new("Could not store scan process state."))?;
        *active = Some(child.id());
    }
    let _scan_guard = ActiveScanGuard {
        pid: active_scan_pid,
    };

    let stdout_pipe = child
        .stdout
        .take()
        .ok_or_else(|| ScanFailure::new("Could not read backend stdout."))?;
    let stderr_pipe = child
        .stderr
        .take()
        .ok_or_else(|| ScanFailure::new("Could not read backend stderr."))?;

    let stderr_handle = thread::spawn(move || {
        let mut stderr = String::new();
        let mut reader = BufReader::new(stderr_pipe);
        let _ = reader.read_to_string(&mut stderr);
        stderr
    });

    let mut stdout = String::new();
    let stdout_reader = BufReader::new(stdout_pipe);

    for line_result in stdout_reader.lines() {
        let line = line_result.map_err(|error| ScanFailure::new(error.to_string()))?;
        let progress_line = line.trim_start();

        if progress_line.starts_with("MIMIR_PROGRESS") {
            let _ = window.emit(
                "mimir-progress",
                ScanProgressLine {
                    line: progress_line.to_string(),
                },
            );
        }

        stdout.push_str(&line);
        stdout.push('\n');
    }

    let status = child
        .wait()
        .map_err(|error| ScanFailure::new(error.to_string()))?;

    let stderr = stderr_handle
        .join()
        .unwrap_or_else(|_| String::from("Could not read backend stderr."));

    if !status.success() {
        return Err(ScanFailure::with_output(
            "Backend scan failed.",
            stdout,
            stderr,
        ));
    }

    if use_enhanced_ai {
        spawn_ai_enrichment(
            app.clone(),
            window.clone(),
            runtime.session_path.clone(),
            vision_model.clone(),
            ai_review_budget,
            ai_timeout_sec,
        );
    }

    Ok(ScanResult {
        stdout,
        stderr,
        session_path: runtime.session_path.to_string_lossy().to_string(),
        latest_session_path: runtime.session_path.to_string_lossy().to_string(),
        latest_session_modified_time: file_modified_time(&runtime.session_path),
        active_output_dir: output_dir.to_string_lossy().to_string(),
        output_argument_used: output_argument_name(&runtime).to_string(),
        backend_mode: runtime.mode_label().to_string(),
        backend_runner: runtime.runner_label().to_string(),
        backend_command,
    })
}

fn count_mp4_files(folder: &Path) -> Result<usize, ScanFailure> {
    let mut count = 0;

    for entry in fs::read_dir(folder).map_err(|error| ScanFailure::new(error.to_string()))? {
        let entry = entry.map_err(|error| ScanFailure::new(error.to_string()))?;
        let path = entry.path();

        if path.is_dir() {
            count += count_mp4_files(&path)?;
            continue;
        }

        let is_mp4 = path
            .extension()
            .and_then(|extension| extension.to_str())
            .map(|extension| extension.eq_ignore_ascii_case("mp4"))
            .unwrap_or(false);

        if is_mp4 {
            count += 1;
        }
    }

    Ok(count)
}

#[tauri::command]
async fn count_teslacam_clips(selected_folder: String) -> Result<usize, ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || {
        let source_folder = PathBuf::from(selected_folder);

        if !source_folder.exists() || !source_folder.is_dir() {
            return Err(ScanFailure::new("Selected folder could not be found."));
        }

        count_mp4_files(&source_folder)
    })
    .await
    .map_err(|error| ScanFailure::new(error.to_string()))?
}

#[tauri::command]
async fn run_local_scan(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    selected_folder: String,
    scan_mode: String,
    use_enhanced_ai: bool,
    vision_model: Option<String>,
    ai_review_budget: Option<u32>,
    ai_timeout_sec: Option<u32>,
    active_scan: tauri::State<'_, ActiveScanProcess>,
) -> Result<ScanResult, ScanFailure> {
    let active_scan_pid = active_scan.pid.clone();
    tauri::async_runtime::spawn_blocking(move || {
        run_scan_sync(
            app,
            window,
            selected_folder,
            scan_mode,
            use_enhanced_ai,
            vision_model,
            ai_review_budget,
            ai_timeout_sec,
            active_scan_pid,
        )
    })
    .await
    .map_err(|error| ScanFailure::new(error.to_string()))?
}

#[tauri::command]
async fn cancel_local_scan(
    active_scan: tauri::State<'_, ActiveScanProcess>,
) -> Result<bool, ScanFailure> {
    let pid = active_scan
        .pid
        .lock()
        .map_err(|_| ScanFailure::new("Could not access scan process state."))?
        .to_owned();
    let Some(pid) = pid else {
        return Ok(false);
    };
    let status = Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .status()
        .map_err(|error| ScanFailure::new(format!("Could not stop the scan: {}", error)))?;
    Ok(status.success())
}

fn run_core_v2_storage_action_sync(
    app: tauri::AppHandle,
    incident_id: String,
    action: String,
) -> Result<StorageActionResult, ScanFailure> {
    if action != "move_to_library" && action != "move_to_trash" && action != "restore_from_trash" {
        return Err(ScanFailure::new("Unsupported storage action."));
    }

    let runtime = resolve_core_v2_actions_runtime(&app)?;
    let mut command = backend_command(&runtime);

    if runtime.is_python_fallback() {
        command.arg(DEV_CORE_V2_ACTION_SCRIPT);
    }

    let output_dir = active_output_dir(&runtime);
    let report_path = output_dir.join("last_action_report.json");
    let journal_path = output_dir.join("storage_action_journal.json");
    command
        .arg("--session")
        .arg(&runtime.session_path)
        .arg("--report")
        .arg(&report_path)
        .arg("--journal")
        .arg(&journal_path)
        .arg("--incident-id")
        .arg(&incident_id);

    if action == "move_to_library" {
        command.arg("--move-to-library");
    } else if action == "move_to_trash" {
        command.arg("--move-to-trash");
    } else {
        command.arg("--restore-from-trash");
    }

    let _ = fs::remove_file(&report_path);

    let output = command
        .output()
        .map_err(|error| ScanFailure::new(format!("Storage action could not start. {}", error)))?;
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    let report_json = fs::read_to_string(&report_path).unwrap_or_else(|_| stdout.clone());
    let parsed: Value = serde_json::from_str(&report_json).unwrap_or_else(|_| json!({}));
    let ok = parsed.get("ok").and_then(Value::as_bool).unwrap_or(false);
    let failed_count = parsed
        .get("failed_files")
        .and_then(Value::as_array)
        .map(|items| items.len())
        .unwrap_or(0);
    let moved_count = parsed
        .get("moved_files")
        .and_then(Value::as_array)
        .map(|items| items.len())
        .unwrap_or(0);

    if !output.status.success() && !report_path.exists() {
        return Err(ScanFailure::with_output(
            "Storage action failed.",
            stdout,
            stderr,
        ));
    }

    let message = if ok && action == "move_to_library" {
        "Moved to Mimir Library.".to_string()
    } else if ok && action == "move_to_trash" {
        "Moved to Mimir Trash.".to_string()
    } else if ok && action == "restore_from_trash" {
        "Restored from Mimir Trash.".to_string()
    } else if moved_count > 0 && failed_count > 0 {
        "Some files could not be moved.".to_string()
    } else {
        "Storage action failed.".to_string()
    };

    Ok(StorageActionResult {
        ok,
        action,
        incident_id,
        message,
        updated_session: runtime.session_path.to_string_lossy().to_string(),
        report_json,
        backend_runner: runtime.runner_label().to_string(),
        stdout,
        stderr,
    })
}

#[tauri::command]
async fn run_core_v2_storage_action(
    app: tauri::AppHandle,
    incident_id: String,
    action: String,
) -> Result<StorageActionResult, ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || {
        run_core_v2_storage_action_sync(app, incident_id, action)
    })
    .await
    .map_err(|error| ScanFailure::new(error.to_string()))?
}

fn save_incident_note_sync(
    app: tauri::AppHandle,
    incident_id: String,
    note: String,
) -> Result<ClipActionResult, ScanFailure> {
    let runtime = resolve_backend_runtime(&app)?;
    let session_path = runtime.session_path;
    let contents =
        fs::read_to_string(&session_path).map_err(|error| ScanFailure::new(error.to_string()))?;
    let mut session: Value =
        serde_json::from_str(&contents).map_err(|error| ScanFailure::new(error.to_string()))?;
    let incidents = session
        .get_mut("incidents")
        .and_then(Value::as_array_mut)
        .ok_or_else(|| ScanFailure::new("latest_session.json has no incidents list."))?;

    let mut updated_incident: Option<Value> = None;

    for (index, incident) in incidents.iter_mut().enumerate() {
        if incident_matches(incident, &incident_id, index) {
            let object = incident
                .as_object_mut()
                .ok_or_else(|| ScanFailure::new("Incident entry is not an object."))?;
            object.insert("user_note".to_string(), Value::String(note.clone()));
            object.insert(
                "user_note_updated_at".to_string(),
                Value::String(chrono_like_now()),
            );
            let log = object
                .entry("user_action_log")
                .or_insert_with(|| Value::Array(Vec::new()));
            if let Some(actions) = log.as_array_mut() {
                actions.push(json!({
                  "action": "set_note",
                  "created_at": chrono_like_now()
                }));
            }
            updated_incident = Some(Value::Object(object.clone()));
            break;
        }
    }

    let updated_incident =
        updated_incident.ok_or_else(|| ScanFailure::new("Incident was not found."))?;
    write_json_atomically(&session_path, &session)?;

    if let Some(path) = incident_json_path(&updated_incident) {
        let _ = write_json_atomically(&path, &updated_incident);
    }

    Ok(ClipActionResult {
        ok: true,
        action: "set_note".to_string(),
        incident_id,
        message: "Note saved.".to_string(),
        updated_session: session_path.to_string_lossy().to_string(),
        stdout: String::new(),
        stderr: String::new(),
    })
}

fn chrono_like_now() -> String {
    match std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH) {
        Ok(duration) => format!("unix:{}", duration.as_secs()),
        Err(_) => "unix:0".to_string(),
    }
}

#[tauri::command]
async fn save_incident_note(
    app: tauri::AppHandle,
    incident_id: String,
    note: String,
) -> Result<ClipActionResult, ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || save_incident_note_sync(app, incident_id, note))
        .await
        .map_err(|error| ScanFailure::new(error.to_string()))?
}

fn save_manual_status_sync(
    app: tauri::AppHandle,
    incident_id: String,
    status: String,
) -> Result<ClipActionResult, ScanFailure> {
    if !valid_status(&status) {
        return Err(ScanFailure::new(
            "Status must be IGNORE, REVIEW, or IMPORTANT.",
        ));
    }
    let session_path = configured_output_dir(&app).join("latest_session.json");
    let contents =
        fs::read_to_string(&session_path).map_err(|error| ScanFailure::new(error.to_string()))?;
    let mut session: Value =
        serde_json::from_str(&contents).map_err(|error| ScanFailure::new(error.to_string()))?;
    let incidents = session
        .get_mut("incidents")
        .and_then(Value::as_array_mut)
        .ok_or_else(|| ScanFailure::new("latest_session.json has no incidents list."))?;
    let mut updated_incident: Option<Value> = None;
    for (index, incident) in incidents.iter_mut().enumerate() {
        if !incident_matches(incident, &incident_id, index) {
            continue;
        }
        let object = incident
            .as_object_mut()
            .ok_or_else(|| ScanFailure::new("Incident entry is not an object."))?;
        let mimir_status = object
            .get("final_severity")
            .and_then(Value::as_str)
            .unwrap_or("IGNORE")
            .to_string();
        let is_override = status != mimir_status;
        object.insert(
            "manual_status_override".to_string(),
            Value::Bool(is_override),
        );
        if is_override {
            object.insert("user_status".to_string(), Value::String(status.clone()));
        } else {
            object.remove("user_status");
        }
        object.insert(
            "user_status_updated_at".to_string(),
            Value::String(chrono_like_now()),
        );
        updated_incident = Some(Value::Object(object.clone()));
        break;
    }
    let updated_incident =
        updated_incident.ok_or_else(|| ScanFailure::new("Incident was not found."))?;
    write_json_atomically(&session_path, &session)?;
    if let Some(path) = incident_json_path(&updated_incident) {
        let _ = write_json_atomically(&path, &updated_incident);
    }
    Ok(ClipActionResult {
        ok: true,
        action: "set_status".to_string(),
        incident_id,
        message: "Status saved for this session.".to_string(),
        updated_session: session_path.to_string_lossy().to_string(),
        stdout: String::new(),
        stderr: String::new(),
    })
}

#[tauri::command]
async fn save_manual_status(
    app: tauri::AppHandle,
    incident_id: String,
    status: String,
) -> Result<ClipActionResult, ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || save_manual_status_sync(app, incident_id, status))
        .await
        .map_err(|error| ScanFailure::new(error.to_string()))?
}

fn save_key_moment_correction_sync(
    app: tauri::AppHandle,
    incident_id: String,
    time_sec: f64,
) -> Result<ClipActionResult, ScanFailure> {
    if !time_sec.is_finite() || time_sec < 0.0 {
        return Err(ScanFailure::new(
            "The corrected key-moment time is invalid.",
        ));
    }
    let session_path = configured_output_dir(&app).join("latest_session.json");
    let contents =
        fs::read_to_string(&session_path).map_err(|error| ScanFailure::new(error.to_string()))?;
    let mut session: Value =
        serde_json::from_str(&contents).map_err(|error| ScanFailure::new(error.to_string()))?;
    let incidents = session
        .get_mut("incidents")
        .and_then(Value::as_array_mut)
        .ok_or_else(|| ScanFailure::new("latest_session.json has no incidents list."))?;
    let mut updated_incident: Option<Value> = None;
    for (index, incident) in incidents.iter_mut().enumerate() {
        if !incident_matches(incident, &incident_id, index) {
            continue;
        }
        let object = incident
            .as_object_mut()
            .ok_or_else(|| ScanFailure::new("Incident entry is not an object."))?;
        object.insert(
            "user_key_moment_sec".to_string(),
            json!((time_sec * 1000.0).round() / 1000.0),
        );
        object.insert(
            "user_key_moment_updated_at".to_string(),
            Value::String(chrono_like_now()),
        );
        updated_incident = Some(Value::Object(object.clone()));
        break;
    }
    let updated_incident =
        updated_incident.ok_or_else(|| ScanFailure::new("Incident was not found."))?;
    write_json_atomically(&session_path, &session)?;
    if let Some(path) = incident_json_path(&updated_incident) {
        let _ = write_json_atomically(&path, &updated_incident);
    }
    Ok(ClipActionResult {
        ok: true,
        action: "set_key_moment".to_string(),
        incident_id,
        message: "Actual moment saved for this session.".to_string(),
        updated_session: session_path.to_string_lossy().to_string(),
        stdout: String::new(),
        stderr: String::new(),
    })
}

#[tauri::command]
async fn save_key_moment_correction(
    app: tauri::AppHandle,
    incident_id: String,
    time_sec: f64,
) -> Result<ClipActionResult, ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || {
        save_key_moment_correction_sync(app, incident_id, time_sec)
    })
    .await
    .map_err(|error| ScanFailure::new(error.to_string()))?
}

#[tauri::command]
async fn save_incident_feedback(
    feedback: Value,
    include_video: bool,
    video_path: Option<String>,
) -> Result<IncidentFeedbackResult, ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || {
        save_incident_feedback_sync(feedback, include_video, video_path)
    })
    .await
    .map_err(|error| ScanFailure::new(error.to_string()))?
}

#[tauri::command]
async fn check_system_requirements(
    app: tauri::AppHandle,
) -> Result<SystemCheckResult, ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || run_system_check_sync(app))
        .await
        .map_err(|error| ScanFailure::new(error.to_string()))
}

#[tauri::command]
async fn check_local_ai(selected_model: Option<String>) -> Result<LocalAiStatus, ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || local_ai_status_sync(selected_model))
        .await
        .map_err(|error| ScanFailure::new(error.to_string()))
}

#[tauri::command]
async fn pull_local_ai_model(
    window: tauri::WebviewWindow,
    selected_model: Option<String>,
) -> Result<LocalAiInstallResult, ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || pull_local_ai_model_sync(window, selected_model))
        .await
        .map_err(|error| ScanFailure::new(error.to_string()))?
}

#[tauri::command]
async fn open_local_ai_download_page() -> Result<(), ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || {
        Command::new("explorer")
            .arg(OLLAMA_DOWNLOAD_URL)
            .spawn()
            .map_err(|error| ScanFailure::new(error.to_string()))?;

        Ok(())
    })
    .await
    .map_err(|error| ScanFailure::new(error.to_string()))?
}

#[tauri::command]
async fn load_latest_session_json(
    app: tauri::AppHandle,
    session_path: Option<String>,
) -> Result<String, ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || {
        let path = validated_session_path(&app, session_path)?;

        let contents =
            fs::read_to_string(&path).map_err(|error| ScanFailure::new(error.to_string()))?;
        if let Ok(session) = serde_json::from_str::<Value>(&contents) {
            allow_session_assets(&app, &session)?;
        }
        Ok(contents)
    })
    .await
    .map_err(|error| ScanFailure::new(error.to_string()))?
}

fn allow_session_assets(app: &tauri::AppHandle, session: &Value) -> Result<(), ScanFailure> {
    let scope = app.asset_protocol_scope();
    let output_dir = configured_output_dir(app);
    scope
        .allow_directory(&output_dir, true)
        .map_err(|error| ScanFailure::new(error.to_string()))?;
    if let Some(source) = session
        .get("scan_summary")
        .and_then(|value| value.get("source_path"))
        .and_then(Value::as_str)
        .or_else(|| session.get("selected_input").and_then(Value::as_str))
    {
        let source_path = PathBuf::from(source);
        if source_path.exists() && source_path.is_dir() {
            scope
                .allow_directory(source_path, true)
                .map_err(|error| ScanFailure::new(error.to_string()))?;
        }
    }
    if let Some(incidents) = session.get("incidents").and_then(Value::as_array) {
        for incident in incidents {
            for field in ["video_path", "thumbnail", "hero_thumbnail", "contact_sheet"] {
                if let Some(value) = incident.get(field).and_then(Value::as_str) {
                    let path = PathBuf::from(value);
                    if path.exists() && path.is_file() {
                        scope
                            .allow_file(path)
                            .map_err(|error| ScanFailure::new(error.to_string()))?;
                    }
                }
            }
            if let Some(clips) = incident.get("camera_clips").and_then(Value::as_array) {
                for clip in clips {
                    if let Some(value) = clip.get("path").and_then(Value::as_str) {
                        let path = PathBuf::from(value);
                        if path.exists() && path.is_file() {
                            scope
                                .allow_file(path)
                                .map_err(|error| ScanFailure::new(error.to_string()))?;
                        }
                    }
                }
            }
        }
    }
    Ok(())
}

#[tauri::command]
async fn list_session_history(
    app: tauri::AppHandle,
) -> Result<Vec<SessionHistoryEntry>, ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || list_session_history_sync(&app))
        .await
        .map_err(|error| ScanFailure::new(error.to_string()))?
}

fn export_training_contribution_sync(
    app: tauri::AppHandle,
    session_path: Option<String>,
    incident_id: String,
    output_path: String,
    recorded_by: String,
    rights_basis: String,
    permission_reference: String,
    independent_permission_record: Option<String>,
) -> Result<TrainingContributionResult, ScanFailure> {
    let session = validated_session_path(&app, session_path)?;
    let incident = incident_id.trim();
    if incident.is_empty()
        || incident.len() > 160
        || !incident.chars().all(|value| value.is_ascii_alphanumeric() || matches!(value, '_' | '-'))
    {
        return Err(ScanFailure::new("The selected incident id is invalid."));
    }
    if !matches!(rights_basis.as_str(), "owned" | "explicit_permission" | "public_license") {
        return Err(ScanFailure::new("Choose a valid rights basis."));
    }
    let recorder = recorded_by.trim();
    let permission = permission_reference.trim();
    if recorder.is_empty() || recorder.len() > 200 {
        return Err(ScanFailure::new("Enter the person recording this consent."));
    }
    if permission.is_empty() || permission.len() > 500 {
        return Err(ScanFailure::new("Enter an auditable ownership, permission, or license reference."));
    }
    let output = PathBuf::from(output_path);
    let output_name = output.file_name().and_then(|value| value.to_str()).unwrap_or_default();
    if !output.is_absolute() || !output_name.to_ascii_lowercase().ends_with(".mimir-dataset.age") {
        return Err(ScanFailure::new("Contribution destination must end with .mimir-dataset.age."));
    }
    if output.exists() {
        return Err(ScanFailure::new("Choose a new contribution package filename."));
    }
    let parent = output.parent().ok_or_else(|| ScanFailure::new("Contribution destination is invalid."))?;
    if !parent.exists() || !parent.is_dir() {
        return Err(ScanFailure::new("Contribution destination folder does not exist."));
    }
    let permission_record = independent_permission_record
        .filter(|value| !value.trim().is_empty())
        .map(PathBuf::from);
    if permission_record.as_ref().is_some_and(|path| !path.is_absolute() || !path.is_file()) {
        return Err(ScanFailure::new("Independent permission record could not be read."));
    }

    let runtime = resolve_core_v2_dataset_runtime(&app)?;
    let mut command = backend_command(&runtime);
    if runtime.is_python_fallback() {
        command.arg(DEV_CORE_V2_DATASET_SCRIPT);
    }
    command
        .arg("export-encrypted")
        .arg("--session")
        .arg(&session)
        .arg("--output")
        .arg(&output)
        .arg("--consent-incident")
        .arg(incident)
        .arg("--recorded-by")
        .arg(recorder)
        .arg("--rights-confirmed")
        .arg("--rights-basis")
        .arg(&rights_basis)
        .arg("--permission-reference")
        .arg(permission)
        .arg("--recipient")
        .arg(TRAINING_AGE_RECIPIENT);
    if let Some(path) = permission_record {
        command.arg("--independent-permission-record").arg(path);
    }
    if let Some(age_executable) = age_executable_for_runtime(&app, &runtime) {
        command.env("MIMIR_AGE_EXE", age_executable);
    }
    let preview = command_preview(&command);
    let result = command
        .output()
        .map_err(|error| ScanFailure::new(format!("Could not start the contribution exporter: {error}")))?;
    let stdout = String::from_utf8_lossy(&result.stdout).to_string();
    let stderr = String::from_utf8_lossy(&result.stderr).to_string();
    if !result.status.success() || !output.is_file() {
        return Err(ScanFailure::with_output(
            "The encrypted contribution package could not be created.",
            stdout,
            stderr,
        ));
    }
    Ok(TrainingContributionResult {
        ok: true,
        output_path: output.to_string_lossy().to_string(),
        backend_runner: runtime.runner_label().to_string(),
        backend_command: preview,
        message: "Encrypted package created. Nothing was uploaded; transfer it manually when ready.".to_string(),
    })
}

#[tauri::command]
async fn export_training_contribution(
    app: tauri::AppHandle,
    session_path: Option<String>,
    incident_id: String,
    output_path: String,
    recorded_by: String,
    rights_basis: String,
    permission_reference: String,
    independent_permission_record: Option<String>,
) -> Result<TrainingContributionResult, ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || {
        export_training_contribution_sync(
            app,
            session_path,
            incident_id,
            output_path,
            recorded_by,
            rights_basis,
            permission_reference,
            independent_permission_record,
        )
    })
    .await
    .map_err(|error| ScanFailure::new(error.to_string()))?
}

#[derive(Deserialize)]
struct ReportImage {
    label: String,
    path: String,
}

#[derive(Deserialize)]
struct ReportSection {
    heading: String,
    lines: Vec<String>,
}

fn escape_html(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

fn mime_type_for_extension(path: &Path) -> &'static str {
    match path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_ascii_lowercase()
        .as_str()
    {
        "png" => "image/png",
        "webp" => "image/webp",
        "gif" => "image/gif",
        _ => "image/jpeg",
    }
}

// Builds a self-contained (no external requests, images embedded as data URIs) HTML
// report meant to be shared outside Mimir -- attached to an insurance claim, sent to
// another driver, printed. A light, document-style layout on purpose: the app's own
// dark theme is right for extended screen review, but reads as unprofessional and
// wastes ink/toner in a printed or emailed report.
fn build_incident_report_html(
    title: &str,
    severity_label: &str,
    subtitle: &str,
    generated_at: &str,
    sections: &[ReportSection],
    embedded_images: &[(String, String, String)],
) -> String {
    let severity_class = match severity_label {
        "Important" => "sev-important",
        "Review" => "sev-review",
        _ => "sev-ignored",
    };

    let mut images_html = String::new();
    for (label, mime, data) in embedded_images {
        images_html.push_str(&format!(
            "<figure><img src=\"data:{mime};base64,{data}\" alt=\"{label}\" /><figcaption>{label}</figcaption></figure>\n",
            mime = mime,
            data = data,
            label = escape_html(label),
        ));
    }

    let mut sections_html = String::new();
    for section in sections {
        if section.lines.is_empty() {
            continue;
        }
        let items: String = section
            .lines
            .iter()
            .map(|line| format!("<li>{}</li>", escape_html(line)))
            .collect();
        sections_html.push_str(&format!(
            "<section><h2>{heading}</h2><ul>{items}</ul></section>\n",
            heading = escape_html(&section.heading),
            items = items,
        ));
    }

    format!(
        r#"<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>{title} - Mimir incident report</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 40px;
    background: #ffffff;
    color: #1a1c1b;
    font-family: -apple-system, "Segoe UI", ui-sans-serif, system-ui, sans-serif;
    line-height: 1.5;
  }}
  .wrap {{ max-width: 760px; margin: 0 auto; }}
  .masthead {{
    display: flex; align-items: baseline; justify-content: space-between;
    border-bottom: 2px solid #111; padding-bottom: 14px; margin-bottom: 24px;
  }}
  .brand {{ font-size: 13px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase; color: #4b5f56; }}
  .generated {{ font-size: 12px; color: #6b6b6b; }}
  h1 {{ font-size: 26px; margin: 0 0 6px; }}
  .subtitle {{ color: #4a4a4a; font-size: 14px; margin: 0 0 18px; }}
  .badge {{
    display: inline-block; font-size: 12px; font-weight: 700; letter-spacing: 0.02em;
    padding: 4px 12px; border-radius: 999px; margin-bottom: 22px;
  }}
  .sev-important {{ background: #f6dedc; color: #8a2f27; }}
  .sev-review {{ background: #f5ecd8; color: #7a5a17; }}
  .sev-ignored {{ background: #e7e9e8; color: #4a4f4d; }}
  figure {{
    display: inline-block; width: 47%; margin: 0 1.5% 18px 0; vertical-align: top;
  }}
  figure img {{ width: 100%; border-radius: 6px; border: 1px solid #ddd; display: block; }}
  figcaption {{ font-size: 11px; color: #666; margin-top: 4px; }}
  section {{ margin: 22px 0; }}
  section h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: 0.1em; color: #4b5f56; margin: 0 0 8px; }}
  section ul {{ margin: 0; padding-left: 20px; font-size: 14px; }}
  section li {{ margin-bottom: 4px; }}
  footer {{ margin-top: 36px; padding-top: 14px; border-top: 1px solid #ddd; font-size: 11px; color: #888; }}
  @media print {{
    body {{ padding: 0; }}
    figure {{ break-inside: avoid; }}
  }}
</style>
</head>
<body>
  <div class="wrap">
    <div class="masthead">
      <div class="brand">Mimir incident report</div>
      <div class="generated">Generated {generated_at}</div>
    </div>
    <h1>{title}</h1>
    <p class="subtitle">{subtitle}</p>
    <div class="badge {severity_class}">{severity_label}</div>
    <div>{images_html}</div>
    {sections_html}
    <footer>
      Produced locally by Mimir from local evidence only. This report documents Mimir's
      local analysis of the selected clip; it does not prove fault, identify people, or
      constitute a legal or insurance determination. Video-space overlap or proximity
      shows apparent visual contact, not confirmed physical contact.
    </footer>
  </div>
</body>
</html>
"#,
        title = escape_html(title),
        generated_at = escape_html(generated_at),
        subtitle = escape_html(subtitle),
        severity_class = severity_class,
        severity_label = escape_html(severity_label),
        images_html = images_html,
        sections_html = sections_html,
    )
}

#[tauri::command]
async fn export_incident_report(
    incident_id: String,
    title: String,
    severity_label: String,
    subtitle: String,
    generated_at: String,
    sections: Vec<ReportSection>,
    images: Vec<ReportImage>,
) -> Result<String, ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || {
        let documents_root = default_documents_root()?;
        let reports_dir = documents_root.join("Mimir Reports");
        fs::create_dir_all(&reports_dir)
            .map_err(|error| ScanFailure::new(format!("Could not create the reports folder: {error}")))?;

        let mut embedded_images = Vec::new();
        for image in &images {
            let path = PathBuf::from(&image.path);
            if !path.is_file() {
                continue;
            }
            let bytes = match fs::read(&path) {
                Ok(bytes) => bytes,
                Err(_) => continue,
            };
            let mime = mime_type_for_extension(&path);
            let data = base64::engine::general_purpose::STANDARD.encode(bytes);
            embedded_images.push((image.label.clone(), mime.to_string(), data));
        }

        let html = build_incident_report_html(&title, &severity_label, &subtitle, &generated_at, &sections, &embedded_images);

        let filename = format!(
            "{}_{}.html",
            safe_filename(&incident_id),
            safe_filename(&chrono_like_now()),
        );
        let report_path = reports_dir.join(filename);
        fs::write(&report_path, html)
            .map_err(|error| ScanFailure::new(format!("Could not write the report file: {error}")))?;

        open_folder_with_explorer(&report_path)?;

        Ok(report_path.to_string_lossy().to_string())
    })
    .await
    .map_err(|error| ScanFailure::new(format!("Report export task failed: {error}")))?
}

#[tauri::command]
async fn open_containing_folder(path: String) -> Result<(), ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || {
        let path = PathBuf::from(path);

        if path.exists() && path.is_dir() {
            return open_folder_with_explorer(&path);
        }

        let folder = path
            .parent()
            .ok_or_else(|| ScanFailure::new("Could not resolve a folder for this path."))?;

        if !folder.exists() {
            return Err(ScanFailure::new("Folder does not exist."));
        }

        open_folder_with_explorer(folder)
    })
    .await
    .map_err(|error| ScanFailure::new(error.to_string()))?
}

#[tauri::command]
async fn open_mimir_storage_folder(kind: String) -> Result<(), ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || {
        let mut folder = default_mimir_library_root()?;

        if kind == "trash" {
            folder = folder.join("_Mimir Trash");
        } else if kind != "library" {
            return Err(ScanFailure::new("Unknown Mimir storage folder."));
        }

        fs::create_dir_all(&folder).map_err(|error| ScanFailure::new(error.to_string()))?;
        open_folder_with_explorer(&folder)
    })
    .await
    .map_err(|error| ScanFailure::new(error.to_string()))?
}

#[tauri::command]
async fn log_incident_diagnostic(
    incident_id: String,
    attempted_video_path: String,
    error_message: String,
    stack_trace: String,
) -> Result<(), ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || {
        append_app_crash_log_sync(
            incident_id,
            attempted_video_path,
            error_message,
            stack_trace,
        )
    })
    .await
    .map_err(|error| ScanFailure::new(error.to_string()))?
}

#[cfg(test)]
mod tests {
    use super::*;

    fn command_args(command: &Command) -> Vec<String> {
        command
            .get_args()
            .map(|value| value.to_string_lossy().to_string())
            .collect()
    }

    #[test]
    fn scan_modes_and_statuses_are_allowlisted() {
        assert!(valid_scan_mode("balanced"));
        assert!(valid_scan_mode("thorough"));
        assert!(!valid_scan_mode("../../unsafe"));
        assert!(valid_status("IMPORTANT"));
        assert!(!valid_status("important; remove"));
    }

    #[test]
    fn local_only_scan_does_not_append_ai_arguments() {
        let mut command = Command::new("scanner");
        append_experimental_ai_scan_args(&mut command, false, "qwen2.5vl:7b", Some(999), Some(120));
        assert!(command_args(&command).is_empty());
    }

    #[test]
    fn experimental_ai_arguments_are_explicit() {
        let mut command = Command::new("scanner");
        append_experimental_ai_scan_args(&mut command, true, "qwen2.5vl:7b", Some(999), Some(120));
        assert_eq!(
            command_args(&command),
            [
                "--vlm",
                "qwen2.5vl:7b",
                "--ai-review-budget",
                "999",
                "--ai-timeout-sec",
                "120",
                "--defer-ai",
            ]
        );
    }

    #[test]
    fn backend_resource_path_matches_where_the_bundler_actually_places_files() {
        // Regression test for a bug where a packaged (release) build could never find
        // its bundled scanner: resource_dir() on Windows is the exe's own directory,
        // not a "resources" subfolder inside it, so that segment must be added back.
        let resolved = backend_resource_path(Path::new(r"C:\Program Files\Mimir"), "mimir-core-v2-scan.exe");
        assert_eq!(
            resolved,
            Path::new(r"C:\Program Files\Mimir\resources\mimir-backend\mimir-core-v2-scan.exe")
        );
    }

    #[test]
    fn report_html_escaping_neutralizes_markup() {
        let escaped = escape_html("<script>alert(\"hi\")</script> & more");
        assert!(!escaped.contains('<'));
        assert!(!escaped.contains('>'));
        assert!(escaped.contains("&lt;script&gt;"));
        assert!(escaped.contains("&amp;"));
        assert!(escaped.contains("&quot;"));
    }

    #[test]
    fn report_image_mime_type_matches_extension() {
        assert_eq!(mime_type_for_extension(Path::new("frame.png")), "image/png");
        assert_eq!(mime_type_for_extension(Path::new("frame.WEBP")), "image/webp");
        assert_eq!(mime_type_for_extension(Path::new("frame.jpg")), "image/jpeg");
        assert_eq!(mime_type_for_extension(Path::new("frame")), "image/jpeg");
    }

    #[test]
    fn report_html_embeds_content_and_escapes_untrusted_fields() {
        let sections = vec![ReportSection {
            heading: "Local evidence".to_string(),
            lines: vec!["<b>not bold</b>".to_string()],
        }];
        let images = vec![("Key frame".to_string(), "image/jpeg".to_string(), "AAAA".to_string())];
        let html = build_incident_report_html(
            "Parked <incident>",
            "Important",
            "12:00 - front.mp4",
            "2026-07-23 12:00",
            &sections,
            &images,
        );

        assert!(html.contains("Parked &lt;incident&gt;"));
        assert!(html.contains("sev-important"));
        assert!(html.contains("data:image/jpeg;base64,AAAA"));
        assert!(html.contains("&lt;b&gt;not bold&lt;/b&gt;"));
        assert!(!html.contains("<incident>"));
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(ActiveScanProcess::default())
        .setup(|app| {
            if let Some(window) = app.get_webview_window("main") {
                spawn_teslacam_drive_watcher(window);
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            check_system_requirements,
            count_teslacam_clips,
            run_local_scan,
            cancel_local_scan,
            run_core_v2_storage_action,
            save_incident_note,
            save_manual_status,
            save_key_moment_correction,
            save_incident_feedback,
            check_local_ai,
            pull_local_ai_model,
            open_local_ai_download_page,
            load_latest_session_json,
            list_session_history,
            export_training_contribution,
            export_incident_report,
            open_containing_folder,
            open_mimir_storage_folder,
            log_incident_diagnostic
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
