#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use serde_json::{json, Value};
use std::{
    fs,
    io::{BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
};

#[allow(dead_code)]
const DEV_BACKEND_ROOT: &str = r"C:\Mimir_Backend";
#[allow(dead_code)]
const DEV_BACKEND_DIST: &str = r"C:\Mimir_Backend\dist\mimir-backend";
#[allow(dead_code)]
const DEV_BACKEND_PYTHON: &str = r"C:\Mimir_Backend\.venv\Scripts\python.exe";
#[allow(dead_code)]
const DEV_BACKEND_SCRIPT: &str = r"C:\Mimir_Backend\tesla_ai_sorter.py";
#[allow(dead_code)]
const DEV_CORE_V2_SCRIPT: &str = r"C:\Mimir_Backend\mimir_core_v2_scan.py";
#[allow(dead_code)]
const DEV_CORE_V2_ACTION_SCRIPT: &str = r"C:\Mimir_Backend\mimir_core_v2_actions.py";
#[allow(dead_code)]
const DEV_CLIP_ACTION_SCRIPT: &str = r"C:\Mimir_Backend\mimir_clip_actions.py";
#[allow(dead_code)]
const DEV_LATEST_SESSION_JSON: &str = r"C:\Mimir_Backend\MimirOutput\latest_session.json";
#[allow(dead_code)]
const DEV_CORE_V2_LATEST_SESSION_JSON: &str = r"C:\Mimir_Backend\MimirOutputV2\latest_session.json";
#[allow(dead_code)]
const DEV_CORE_V2_ACTION_REPORT_JSON: &str =
    r"C:\Mimir_Backend\MimirOutputV2\last_action_report.json";
const USE_MIMIR_CORE_V2: bool = true;
const BACKEND_RESOURCE_FOLDER: &str = "mimir-backend";
const BACKEND_EXE_NAME: &str = "mimir-backend.exe";
const DEFAULT_VISION_MODEL: &str = "qwen2.5vl:7b";
const OLLAMA_DOWNLOAD_URL: &str = "https://ollama.com/download";

#[derive(Serialize)]
struct ScanResult {
    stdout: String,
    stderr: String,
    session_path: String,
    backend_mode: String,
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
struct ScanFailure {
    message: String,
    stdout: String,
    stderr: String,
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
    Bundled,
    #[allow(dead_code)]
    DevelopmentResource,
    #[allow(dead_code)]
    DevelopmentPythonFallback,
    #[allow(dead_code)]
    CoreV2Python,
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
            BackendMode::Bundled => "bundled_backend",
            BackendMode::DevelopmentResource => "development_resource_backend",
            BackendMode::DevelopmentPythonFallback => "development_backend",
            BackendMode::CoreV2Python => "mimir_core_v2",
        }
    }

    fn is_python_fallback(&self) -> bool {
        matches!(
            self.mode,
            BackendMode::DevelopmentPythonFallback | BackendMode::CoreV2Python
        )
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

fn bundled_session_path(backend_dir: &Path) -> PathBuf {
    let output_folder = if USE_MIMIR_CORE_V2 {
        "MimirOutputV2"
    } else {
        "MimirOutput"
    };

    backend_dir
        .join("_internal")
        .join(output_folder)
        .join("latest_session.json")
}

fn bundled_backend_runtime_from_dir(
    backend_dir: PathBuf,
    mode: BackendMode,
) -> Option<BackendRuntime> {
    let executable = backend_dir.join(BACKEND_EXE_NAME);

    if !executable.exists() {
        return None;
    }

    Some(BackendRuntime {
        executable,
        current_dir: backend_dir.clone(),
        session_path: bundled_session_path(&backend_dir),
        mode,
    })
}

fn resolve_backend_runtime(app: &tauri::AppHandle) -> Result<BackendRuntime, ScanFailure> {
    if USE_MIMIR_CORE_V2
        && Path::new(DEV_BACKEND_PYTHON).exists()
        && Path::new(DEV_CORE_V2_SCRIPT).exists()
    {
        return Ok(BackendRuntime {
            executable: PathBuf::from(DEV_BACKEND_PYTHON),
            current_dir: PathBuf::from(DEV_BACKEND_ROOT),
            session_path: PathBuf::from(DEV_CORE_V2_LATEST_SESSION_JSON),
            mode: BackendMode::CoreV2Python,
        });
    }

    #[cfg(debug_assertions)]
    {
        if let Some(runtime) = bundled_backend_runtime_from_dir(
            PathBuf::from(DEV_BACKEND_DIST),
            BackendMode::DevelopmentResource,
        ) {
            return Ok(runtime);
        }
    }

    if let Some(resource_dir) = app.path_resolver().resource_dir() {
        if let Some(runtime) = bundled_backend_runtime_from_dir(
            resource_dir.join(BACKEND_RESOURCE_FOLDER),
            BackendMode::Bundled,
        ) {
            return Ok(runtime);
        }
    }

    #[cfg(debug_assertions)]
    {
        let dev_resource_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("resources")
            .join(BACKEND_RESOURCE_FOLDER);

        if let Some(runtime) =
            bundled_backend_runtime_from_dir(dev_resource_dir, BackendMode::DevelopmentResource)
        {
            return Ok(runtime);
        }

        if Path::new(DEV_BACKEND_PYTHON).exists() && Path::new(DEV_BACKEND_SCRIPT).exists() {
            return Ok(BackendRuntime {
                executable: PathBuf::from(DEV_BACKEND_PYTHON),
                current_dir: PathBuf::from(DEV_BACKEND_ROOT),
                session_path: PathBuf::from(DEV_LATEST_SESSION_JSON),
                mode: BackendMode::DevelopmentPythonFallback,
            });
        }
    }

    Err(ScanFailure::new("Mimir could not start the local scanner."))
}

fn backend_command(runtime: &BackendRuntime) -> Command {
    let mut command = Command::new(&runtime.executable);
    command.current_dir(&runtime.current_dir);
    command
}

fn append_backend_scan_args(
    command: &mut Command,
    runtime: &BackendRuntime,
    input_folder: &str,
    scan_mode: &str,
    use_enhanced_ai: bool,
    vision_model: &str,
) {
    let backend_scan_mode = if USE_MIMIR_CORE_V2 && scan_mode == "quality" {
        "thorough"
    } else {
        scan_mode
    };

    if runtime.is_python_fallback() {
        let script = if matches!(runtime.mode, BackendMode::CoreV2Python) {
            DEV_CORE_V2_SCRIPT
        } else {
            DEV_BACKEND_SCRIPT
        };

        command
            .arg(script)
            .arg("--input")
            .arg(input_folder)
            .arg("--mode")
            .arg(backend_scan_mode);

        if use_enhanced_ai && !matches!(runtime.mode, BackendMode::CoreV2Python) {
            command.arg("--vlm").arg(vision_model);
        }
    } else {
        command
            .arg("scan")
            .arg("--input")
            .arg(input_folder)
            .arg("--mode")
            .arg(backend_scan_mode);

        if use_enhanced_ai {
            command.arg("--vlm").arg(vision_model);
        }
    }
}

fn append_backend_action_args(
    command: &mut Command,
    runtime: &BackendRuntime,
    incident_id: &str,
    action: &str,
    status: Option<&str>,
) -> Result<(), ScanFailure> {
    if runtime.is_python_fallback() {
        command
            .arg(DEV_CLIP_ACTION_SCRIPT)
            .arg("--session")
            .arg(&runtime.session_path)
            .arg("--incident-id")
            .arg(incident_id);
    } else {
        command
            .arg("action")
            .arg("--session")
            .arg(&runtime.session_path)
            .arg("--incident-id")
            .arg(incident_id);
    }

    match action {
        "set_status" => {
            let status = status.ok_or_else(|| ScanFailure::new("Status is required."))?;
            if !valid_status(status) {
                return Err(ScanFailure::new(
                    "Status must be IGNORE, REVIEW, or IMPORTANT.",
                ));
            }
            command.arg("--set-status").arg(status);
        }
        "move_to_library" => {
            command.arg("--move-to-library");
        }
        "delete" => {
            command.arg("--delete");
        }
        _ => return Err(ScanFailure::new("Unsupported incident action.")),
    }

    Ok(())
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
    Command::new("ollama").arg("list").output()
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
    window: tauri::Window,
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
    window: tauri::Window,
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
    let folder_name = format!("{}_{}", safe_filename(incident_id), safe_filename(&submitted_at));
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

            if !destination.exists() || destination.metadata().map(|meta| meta.len()).unwrap_or(0) == 0 {
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

fn run_system_check_sync(app: tauri::AppHandle) -> SystemCheckResult {
    let mut items = Vec::new();
    let runtime = resolve_backend_runtime(&app);

    let runtime = match runtime {
        Ok(runtime) => runtime,
        Err(error) => {
            items.push(check_item(
                "local_scanner",
                "Local scanner",
                false,
                "Mimir could not start the local scanner.",
                "The scanner is required before Mimir can analyze footage.",
                "Try reinstalling Mimir. Technical details are available below.",
                error.message,
            ));

            return SystemCheckResult {
                ok: false,
                checked_at: chrono_like_now(),
                items,
            };
        }
    };

    let mut command = backend_command(&runtime);

    if runtime.is_python_fallback() {
        let scanner_script = if matches!(runtime.mode, BackendMode::CoreV2Python) {
            DEV_CORE_V2_SCRIPT
        } else {
            DEV_BACKEND_SCRIPT
        };
        let scanner_label = if matches!(runtime.mode, BackendMode::CoreV2Python) {
            "Mimir Core v2"
        } else {
            "Development scanner"
        };

        items.push(check_item(
            "development_backend_mode",
            "Development backend mode",
            true,
            "Development backend mode is active.",
            "This is only used while running Mimir from the development workspace.",
            "Packaged builds use the bundled local scanner.",
            format!(
                "Development fallback command: {} {}",
                DEV_BACKEND_PYTHON, scanner_script
            ),
        ));

        let python_exists = Path::new(DEV_BACKEND_PYTHON).exists();
        let scanner_exists = Path::new(scanner_script).exists();
        items.push(check_item(
            "local_scanner",
            "Local scanner",
            python_exists && scanner_exists,
            if python_exists && scanner_exists {
                format!("{} is available.", scanner_label)
            } else {
                format!("{} is not available.", scanner_label)
            },
            "The scanner is required before Mimir can analyze footage.",
            "Build the bundled backend or restore the development backend.",
            String::from("Development backend mode"),
        ));
    } else {
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
                    details.clone(),
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
                error.to_string(),
            )),
        }
    }

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

fn run_scan_sync(
    app: tauri::AppHandle,
    window: tauri::Window,
    selected_folder: String,
    scan_mode: String,
    use_enhanced_ai: bool,
    vision_model: Option<String>,
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

    let mut command = backend_command(&runtime);
    append_backend_scan_args(
        &mut command,
        &runtime,
        &input_folder,
        &scan_mode,
        use_enhanced_ai,
        &vision_model,
    );

    let mut child = command
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| ScanFailure::new(format!("Bundled backend could not start. {}", error)))?;

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

    Ok(ScanResult {
        stdout,
        stderr,
        session_path: runtime.session_path.to_string_lossy().to_string(),
        backend_mode: runtime.mode_label().to_string(),
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
    window: tauri::Window,
    selected_folder: String,
    scan_mode: String,
    use_enhanced_ai: bool,
    vision_model: Option<String>,
) -> Result<ScanResult, ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || {
        run_scan_sync(
            app,
            window,
            selected_folder,
            scan_mode,
            use_enhanced_ai,
            vision_model,
        )
    })
    .await
    .map_err(|error| ScanFailure::new(error.to_string()))?
}

fn run_clip_action_sync(
    app: tauri::AppHandle,
    incident_id: String,
    action: String,
    status: Option<String>,
) -> Result<ClipActionResult, ScanFailure> {
    let runtime = resolve_backend_runtime(&app)?;
    let mut command = backend_command(&runtime);
    append_backend_action_args(
        &mut command,
        &runtime,
        &incident_id,
        &action,
        status.as_deref(),
    )?;

    let output = command
        .output()
        .map_err(|error| ScanFailure::new(format!("Bundled backend could not start. {}", error)))?;
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();

    if !output.status.success() {
        return Err(ScanFailure::with_output(
            "Incident action failed.",
            stdout,
            stderr,
        ));
    }

    let parsed: Value = serde_json::from_str(&stdout).map_err(|error| {
        ScanFailure::with_output(error.to_string(), stdout.clone(), stderr.clone())
    })?;
    let ok = parsed.get("ok").and_then(Value::as_bool).unwrap_or(false);

    if !ok {
        let message = parsed
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("Incident action failed.");
        return Err(ScanFailure::with_output(message, stdout, stderr));
    }

    Ok(ClipActionResult {
        ok,
        action: parsed
            .get("action")
            .and_then(Value::as_str)
            .unwrap_or(action.as_str())
            .to_string(),
        incident_id: parsed
            .get("incident_id")
            .and_then(Value::as_str)
            .unwrap_or(&incident_id)
            .to_string(),
        message: parsed
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("Incident action completed.")
            .to_string(),
        updated_session: parsed
            .get("updated_session")
            .and_then(Value::as_str)
            .unwrap_or_else(|| runtime.session_path.to_str().unwrap_or(""))
            .to_string(),
        stdout,
        stderr,
    })
}

#[tauri::command]
async fn run_incident_action(
    app: tauri::AppHandle,
    incident_id: String,
    action: String,
    status: Option<String>,
) -> Result<ClipActionResult, ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || {
        run_clip_action_sync(app, incident_id, action, status)
    })
    .await
    .map_err(|error| ScanFailure::new(error.to_string()))?
}

fn run_core_v2_storage_action_sync(
    app: tauri::AppHandle,
    incident_id: String,
    action: String,
) -> Result<StorageActionResult, ScanFailure> {
    if !USE_MIMIR_CORE_V2 {
        return Err(ScanFailure::new(
            "Core v2 storage actions are not enabled in this build.",
        ));
    }

    if action != "move_to_library" && action != "move_to_trash" {
        return Err(ScanFailure::new("Unsupported storage action."));
    }

    let runtime = resolve_backend_runtime(&app)?;
    let python = if matches!(runtime.mode, BackendMode::CoreV2Python) {
        runtime.executable.clone()
    } else if Path::new(DEV_BACKEND_PYTHON).exists() {
        PathBuf::from(DEV_BACKEND_PYTHON)
    } else {
        return Err(ScanFailure::new(
            "Mimir could not find the Core v2 storage action runtime.",
        ));
    };

    if !Path::new(DEV_CORE_V2_ACTION_SCRIPT).exists() {
        return Err(ScanFailure::new(
            "Mimir could not find the Core v2 storage action script.",
        ));
    }

    let mut command = Command::new(python);
    command
        .current_dir(DEV_BACKEND_ROOT)
        .arg(DEV_CORE_V2_ACTION_SCRIPT)
        .arg("--incident-id")
        .arg(&incident_id);

    if action == "move_to_library" {
        command.arg("--move-to-library");
    } else {
        command.arg("--move-to-trash");
    }

    let report_path = PathBuf::from(DEV_CORE_V2_ACTION_REPORT_JSON);
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
        updated_session: DEV_CORE_V2_LATEST_SESSION_JSON.to_string(),
        report_json,
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
    window: tauri::Window,
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
async fn load_latest_session_json(app: tauri::AppHandle) -> Result<String, ScanFailure> {
    tauri::async_runtime::spawn_blocking(move || {
        let runtime = resolve_backend_runtime(&app)?;
        fs::read_to_string(&runtime.session_path)
            .map_err(|error| ScanFailure::new(error.to_string()))
    })
    .await
    .map_err(|error| ScanFailure::new(error.to_string()))?
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

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            check_system_requirements,
            count_teslacam_clips,
            run_local_scan,
            run_incident_action,
            run_core_v2_storage_action,
            save_incident_note,
            save_incident_feedback,
            check_local_ai,
            pull_local_ai_model,
            open_local_ai_download_page,
            load_latest_session_json,
            open_containing_folder,
            open_mimir_storage_folder,
            log_incident_diagnostic
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
