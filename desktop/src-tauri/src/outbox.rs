//! Durable submission queue for feedback and contribution packages.
//!
//! An encrypted package is written to disk here *before* any network attempt
//! is made, and is never deleted regardless of what the upload does. The
//! encrypted file on disk is the durable artifact; `outbox_entry.json` next
//! to it just tracks whether it has been sent yet. A failed upload leaves
//! the entry `pending` for retry -- it never loses the package the way a
//! purely in-memory retry would if the app closed mid-upload.
//!
//! This is a genuinely new concern (a durable queue + retry policy), which
//! is why it lives in its own module instead of growing `main.rs` further.

use crate::{chrono_like_now, default_documents_root, safe_filename, write_json_atomically, ScanFailure};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::path::{Path, PathBuf};
use std::time::Duration;

// A baked-in constant, exactly like TRAINING_AGE_RECIPIENT in main.rs. This
// is a cheap filter against non-Mimir traffic, not real authentication --
// anyone can extract it from the binary. The actual defenses are server-side:
// Cloudflare rate limiting on the route and a hard per-request size cap. Real
// content validation happens where it always has: client-side before
// encryption, and developer-side at intake with the age private key, which
// never touches this constant or the server at all.
pub const OUTBOX_APP_TOKEN: &str = "mimir-beta-2026-a4f9c1";

// Every mode-dependent constant elsewhere in this app resolves from an env
// var with a hardcoded fallback (see MIMIR_OUTPUT_DIR). Same idiom here: a
// real, deployed Worker is now the default, verified end to end (all of
// accept / duplicate-reject / bad-token / bad-content / bad-package-id /
// no-read-access) against C:\MimirDev\ingest-worker on 2026-07-31. Point
// MIMIR_INTAKE_URL at scripts/dev_intake_mock.py's local mock instead for
// development, so nothing built locally submits to production by accident.
fn intake_base_url() -> String {
    std::env::var("MIMIR_INTAKE_URL")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "https://mimir-ingest.mimir-ingest-a4f9c1.workers.dev".to_string())
}

// Beyond this many failed attempts, auto-retry-on-launch stops trying and the
// entry needs a manual "Retry sending" action instead. Without this cap, a
// permanently broken connection would retry-storm on every app launch.
const MAX_AUTO_RETRY_ATTEMPTS: u32 = 5;

// Cloudflare caps request bodies at the edge, before a Worker runs. A real
// 106 MB contribution sent on 2026-08-04 came back `413 Payload Too Large`
// with an HTML body -- the edge error page, not the Worker's JSON -- which is
// why no contribution had ever arrived while small feedback packages had.
//
// Anything larger than one part is now uploaded in chunks and reassembled
// with R2's multipart API, so the edge limit no longer decides what can be
// sent. This must match PART_SIZE in the Worker and in dev_intake_mock.py:
// R2 requires every part but the last to be exactly this size.
const PART_SIZE: u64 = 64 * 1024 * 1024;

// Same env-override idiom as MIMIR_INTAKE_URL and MIMIR_OUTBOX_DIR. Tests use
// a tiny part size so the chunked path can be exercised without writing a
// 64 MB fixture; the mock server is told the same value. Not a production
// knob -- R2 requires real parts to be at least 5 MiB, so anything smaller
// only works against the mock.
fn part_size() -> u64 {
    std::env::var("MIMIR_UPLOAD_PART_SIZE")
        .ok()
        .and_then(|value| value.trim().parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(PART_SIZE)
}

// Still a real ceiling, now the intake service's own per-kind cap rather than
// the edge's. Declared up front at create time so an impossible upload is
// refused before any bytes move.
const MAX_CONTRIBUTION_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const MAX_FEEDBACK_BYTES: u64 = 500 * 1024 * 1024;

fn describe_bytes(bytes: u64) -> String {
    if bytes >= 1_000_000_000 {
        return format!("{:.1} GB", bytes as f64 / 1_000_000_000.0);
    }

    if bytes >= 1_000_000 {
        return format!("{:.0} MB", bytes as f64 / 1_000_000.0);
    }

    format!("{bytes} bytes")
}

/// Turn a server rejection into one readable line.
///
/// The body was previously pasted in raw, so a Cloudflare HTML error page
/// ended up inside the entry's `last_error` and in front of the user. Markup
/// carries nothing actionable, and the page can be arbitrarily long.
fn summarize_server_error(status: reqwest::StatusCode, body: &str) -> String {
    let trimmed = body.trim();
    let looks_like_markup = trimmed.starts_with('<') || trimmed.to_ascii_lowercase().contains("<html");

    if trimmed.is_empty() || looks_like_markup {
        if status == reqwest::StatusCode::PAYLOAD_TOO_LARGE {
            return format!(
                "The submission service refused this as too large ({status}), before Mimir's own \
                 server saw it. Retrying will not help."
            );
        }

        return format!("The submission server declined this ({status}).");
    }

    let condensed: String = trimmed.split_whitespace().collect::<Vec<_>>().join(" ");
    let clipped: String = condensed.chars().take(300).collect();

    format!("The submission server declined this ({status}): {clipped}")
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum SubmissionKind {
    Feedback,
    Contribution,
}

impl SubmissionKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Feedback => "feedback",
            Self::Contribution => "contribution",
        }
    }

    fn route(self) -> &'static str {
        match self {
            Self::Feedback => "/v1/submit/feedback",
            Self::Contribution => "/v1/submit/contribution",
        }
    }

    /// Matches the intake service's per-route cap.
    ///
    /// Overridable by env for the same reason as the part size: proving that
    /// an oversized package is refused should not require writing a 2 GB
    /// fixture. Lowering it client-side only causes an earlier, clearer
    /// refusal, and raising it proves nothing -- the service enforces its own
    /// cap regardless of what the client believes.
    fn max_bytes(self) -> u64 {
        if let Some(override_bytes) = std::env::var("MIMIR_MAX_SUBMISSION_BYTES")
            .ok()
            .and_then(|value| value.trim().parse::<u64>().ok())
            .filter(|value| *value > 0)
        {
            return override_bytes;
        }

        match self {
            Self::Feedback => MAX_FEEDBACK_BYTES,
            Self::Contribution => MAX_CONTRIBUTION_BYTES,
        }
    }

    fn as_create_kind(self) -> &'static str {
        match self {
            Self::Feedback => "feedback",
            Self::Contribution => "contribution",
        }
    }

    pub fn package_suffix(self) -> &'static str {
        match self {
            Self::Feedback => ".mimir-feedback.age",
            Self::Contribution => ".mimir-dataset.age",
        }
    }

    fn from_str(value: &str) -> Option<Self> {
        match value {
            "feedback" => Some(Self::Feedback),
            "contribution" => Some(Self::Contribution),
            _ => None,
        }
    }
}

#[derive(Serialize, Deserialize, Clone)]
pub struct OutboxEntry {
    pub kind: String,
    pub package_id: String,
    pub created_at: String,
    pub attempts: u32,
    pub last_error: String,
    pub status: String, // "pending" | "sent"
    /// Set when the submission can never succeed as it stands -- an oversized
    /// package, or a rejection the server will repeat. Retrying is futile, and
    /// telling the user "Mimir will try again" would be a lie. Defaulted so
    /// entries written before this field existed still load.
    #[serde(default)]
    pub permanent_failure: bool,
}

#[derive(Serialize, Clone)]
pub struct OutboxSubmitResult {
    pub package_id: String,
    // "blocked" means this entry can never be sent as it stands -- its
    // encrypted package is missing, or its kind is unrecognized -- as opposed
    // to "pending", which is a send that failed and is worth retrying.
    pub status: String, // "sent" | "pending" | "blocked"
    pub message: String,
}

pub fn outbox_root() -> Result<PathBuf, ScanFailure> {
    // Same override idiom as MIMIR_OUTPUT_DIR / MIMIR_MODEL_OVERRIDE_DIR
    // elsewhere in this app. Also what lets tests point the Outbox at a
    // throwaway temp directory instead of the real Documents folder.
    if let Some(configured) = std::env::var_os("MIMIR_OUTBOX_DIR") {
        let path = PathBuf::from(configured);
        if path.is_absolute() {
            return Ok(path);
        }
    }
    Ok(default_documents_root()?.join("Mimir Outbox"))
}

pub fn outbox_entry_dir(root: &Path, package_id: &str) -> PathBuf {
    root.join(safe_filename(package_id))
}

pub fn outbox_package_path(entry_dir: &Path, kind: SubmissionKind) -> PathBuf {
    entry_dir.join(format!("package{}", kind.package_suffix()))
}

fn entry_sidecar_path(entry_dir: &Path) -> PathBuf {
    entry_dir.join("outbox_entry.json")
}

fn read_entry(entry_dir: &Path) -> Result<OutboxEntry, ScanFailure> {
    let path = entry_sidecar_path(entry_dir);
    let contents = std::fs::read_to_string(&path)
        .map_err(|_| ScanFailure::new("Could not read this Outbox entry."))?;
    serde_json::from_str(&contents)
        .map_err(|error| ScanFailure::new(format!("This Outbox entry is corrupted: {error}")))
}

fn write_entry(entry_dir: &Path, entry: &OutboxEntry) -> Result<(), ScanFailure> {
    write_json_atomically(&entry_sidecar_path(entry_dir), &json!(entry))
}

/// Reserve a folder for this package inside the Outbox and return its path.
/// The caller (a packaging command) writes the encrypted file directly at
/// `outbox_package_path(&dir, kind)` -- there is no separate temp-then-move
/// step, so the encrypted package exists durably on disk from the moment
/// `age.exe` succeeds.
pub fn reserve_entry_dir(kind: SubmissionKind, package_id: &str) -> Result<PathBuf, ScanFailure> {
    let root = outbox_root()?;
    let entry_dir = outbox_entry_dir(&root, package_id);
    std::fs::create_dir_all(&entry_dir)
        .map_err(|error| ScanFailure::new(format!("Could not create the Outbox folder: {error}")))?;
    let _ = kind;
    Ok(entry_dir)
}

/// Record a pending entry once the encrypted package is on disk. Must be
/// called after the package file exists at `outbox_package_path`, and before
/// any upload is attempted -- this ordering is what makes the package
/// recoverable even if the app is killed mid-upload.
pub fn stage_pending_entry(entry_dir: &Path, kind: SubmissionKind, package_id: &str) -> Result<(), ScanFailure> {
    let entry = OutboxEntry {
        kind: kind.as_str().to_string(),
        package_id: package_id.to_string(),
        created_at: chrono_like_now(),
        attempts: 0,
        last_error: String::new(),
        status: "pending".to_string(),
        permanent_failure: false,
    };
    write_entry(entry_dir, &entry)
}

/// Attempt to upload one Outbox entry. Never deletes the encrypted package,
/// regardless of outcome -- a failed attempt just leaves `status: pending`
/// with the error recorded, ready for the next retry.
pub async fn attempt_upload(entry_dir: &Path) -> Result<OutboxSubmitResult, ScanFailure> {
    let mut entry = read_entry(entry_dir)?;

    if entry.status == "sent" {
        return Ok(OutboxSubmitResult {
            package_id: entry.package_id,
            status: "sent".to_string(),
            message: "Already sent.".to_string(),
        });
    }

    let kind = SubmissionKind::from_str(&entry.kind)
        .ok_or_else(|| ScanFailure::new("This Outbox entry has an unrecognized kind."))?;
    let package_path = outbox_package_path(entry_dir, kind);
    if !package_path.is_file() {
        return Err(ScanFailure::new(
            "The encrypted package for this Outbox entry is missing. It cannot be sent.",
        ));
    }

    let size = match std::fs::metadata(&package_path) {
        Ok(metadata) => metadata.len(),
        Err(error) => {
            return record_failure(entry_dir, &mut entry, format!("Could not read the encrypted package: {error}"));
        }
    };

    // Refuse before spending the upload on something the service will never
    // accept, rather than after transferring it.
    if size > kind.max_bytes() {
        return record_permanent_failure(
            entry_dir,
            &mut entry,
            format!(
                "This package is {} and the submission service refuses anything over {}. \
                 Retrying will not help. Your encrypted copy is kept -- see docs/DATA_CONTRIBUTION.md \
                 for transferring it another way.",
                describe_bytes(size),
                describe_bytes(kind.max_bytes()),
            ),
        );
    }

    // Anything past a single part goes up in chunks: one request per part
    // keeps every one of them under the edge's body limit.
    if size > part_size() {
        return match chunked_upload(kind, &package_path, size, &entry.package_id).await {
            Ok(()) => {
                entry.status = "sent".to_string();
                entry.last_error = String::new();
                write_entry(entry_dir, &entry)?;
                Ok(OutboxSubmitResult {
                    package_id: entry.package_id,
                    status: "sent".to_string(),
                    message: "Sent.".to_string(),
                })
            }
            Err(failure) if failure.permanent => {
                record_permanent_failure(entry_dir, &mut entry, failure.message)
            }
            Err(failure) => record_failure(entry_dir, &mut entry, failure.message),
        };
    }

    let file = match tokio::fs::File::open(&package_path).await {
        Ok(file) => file,
        Err(error) => {
            return record_failure(entry_dir, &mut entry, format!("Could not open the encrypted package: {error}"));
        }
    };

    let client = match reqwest::Client::builder().timeout(Duration::from_secs(300)).build() {
        Ok(client) => client,
        Err(error) => {
            return record_failure(entry_dir, &mut entry, format!("Could not prepare the upload: {error}"));
        }
    };

    let url = format!("{}{}", intake_base_url(), kind.route());
    let response = client
        .post(&url)
        .header("X-Mimir-App-Token", OUTBOX_APP_TOKEN)
        .header("X-Mimir-Package-Id", entry.package_id.as_str())
        .header("Content-Type", "application/octet-stream")
        .header("Content-Length", size)
        // Streamed from disk rather than read into memory first -- contribution
        // packages carry raw video and can be large.
        .body(reqwest::Body::from(file))
        .send()
        .await;

    match response {
        Ok(response) if response.status().is_success() => {
            entry.status = "sent".to_string();
            entry.last_error = String::new();
            write_entry(entry_dir, &entry)?;
            Ok(OutboxSubmitResult {
                package_id: entry.package_id,
                status: "sent".to_string(),
                message: "Sent.".to_string(),
            })
        }
        Ok(response) => {
            let status = response.status();
            let reason = response.text().await.unwrap_or_default();
            let summary = summarize_server_error(status, &reason);

            // A rejection for size or shape will be repeated verbatim on every
            // retry. Anything else (auth, rate limit, server trouble) may well
            // succeed later, so those stay ordinarily retryable.
            if is_permanent_rejection(status) {
                return record_permanent_failure(entry_dir, &mut entry, summary);
            }

            record_failure(entry_dir, &mut entry, summary)
        }
        Err(error) => record_failure(
            entry_dir,
            &mut entry,
            format!("Could not reach the submission server: {error}"),
        ),
    }
}

/// A chunked-upload failure, carrying whether retrying could ever help.
struct UploadFailure {
    message: String,
    permanent: bool,
}

impl UploadFailure {
    fn transient(message: impl Into<String>) -> Self {
        Self { message: message.into(), permanent: false }
    }

    fn permanent(message: impl Into<String>) -> Self {
        Self { message: message.into(), permanent: true }
    }
}

#[derive(Deserialize)]
struct MultipartCreated {
    object_key: String,
    upload_id: String,
    part_size: u64,
}

#[derive(Deserialize)]
struct MultipartPartAck {
    part_number: u32,
    etag: String,
}

fn upload_client() -> Result<reqwest::Client, UploadFailure> {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(300))
        .build()
        .map_err(|error| UploadFailure::transient(format!("Could not prepare the upload: {error}")))
}

/// Upload a package too large for one request, in parts.
///
/// Cloudflare refuses request bodies over its edge limit before the Worker
/// runs, so a contribution -- raw video for every camera angle -- can never
/// arrive in a single POST. Each part is its own request, comfortably under
/// that limit, and the service reassembles them with R2's multipart API.
///
/// A failure part-way through aborts the upload server-side rather than
/// leaving an incomplete object accruing storage. The encrypted package on
/// disk is never touched either way.
async fn chunked_upload(
    kind: SubmissionKind,
    package_path: &Path,
    size: u64,
    package_id: &str,
) -> Result<(), UploadFailure> {
    let client = upload_client()?;
    let base = intake_base_url();

    let create = client
        .post(format!("{base}/v1/multipart/create"))
        .header("X-Mimir-App-Token", OUTBOX_APP_TOKEN)
        .json(&json!({
            "kind": kind.as_create_kind(),
            "package_id": package_id,
            "total_bytes": size,
        }))
        .send()
        .await
        .map_err(|error| UploadFailure::transient(format!("Could not reach the submission server: {error}")))?;

    if !create.status().is_success() {
        let status = create.status();

        // A 404 here means the intake service predates chunked upload -- the
        // route simply is not deployed yet. Left as a bare "404 Not Found"
        // that reads like a bug in Mimir, so name the actual situation. It is
        // transient on purpose: it resolves the moment the service is updated,
        // and the package is kept meanwhile.
        if status == reqwest::StatusCode::NOT_FOUND {
            return Err(UploadFailure::transient(
                "This submission is too large to send in one piece, and the submission service \
                 does not accept chunked uploads yet. Your encrypted copy is kept and Mimir will \
                 try again later.",
            ));
        }

        let body = create.text().await.unwrap_or_default();
        let message = summarize_server_error(status, &body);
        // Same reasoning as the single-shot path: size and shape rejections
        // will simply be repeated, anything else may clear on its own.
        return Err(if is_permanent_rejection(status) {
            UploadFailure::permanent(message)
        } else {
            UploadFailure::transient(message)
        });
    }

    let created: MultipartCreated = create
        .json()
        .await
        .map_err(|error| UploadFailure::transient(format!("The submission server sent an unusable reply: {error}")))?;

    // The server decides the part size; trusting our own constant would break
    // silently the moment the two drift apart.
    let part_size = if created.part_size > 0 { created.part_size } else { part_size() };

    match upload_parts(&client, &base, package_path, part_size, &created).await {
        Ok(parts) => {
            let complete = client
                .post(format!("{base}/v1/multipart/complete"))
                .header("X-Mimir-App-Token", OUTBOX_APP_TOKEN)
                .json(&json!({
                    "object_key": created.object_key,
                    "upload_id": created.upload_id,
                    "parts": parts
                        .iter()
                        .map(|part| json!({ "part_number": part.part_number, "etag": part.etag }))
                        .collect::<Vec<_>>(),
                }))
                .send()
                .await
                .map_err(|error| {
                    UploadFailure::transient(format!("Could not reach the submission server: {error}"))
                })?;

            if complete.status().is_success() {
                return Ok(());
            }

            let status = complete.status();
            let body = complete.text().await.unwrap_or_default();
            Err(UploadFailure::transient(summarize_server_error(status, &body)))
        }
        Err(failure) => {
            // Best effort: a failed abort must not mask the real error.
            let _ = client
                .post(format!("{base}/v1/multipart/abort"))
                .header("X-Mimir-App-Token", OUTBOX_APP_TOKEN)
                .json(&json!({ "object_key": created.object_key, "upload_id": created.upload_id }))
                .send()
                .await;
            Err(failure)
        }
    }
}

async fn upload_parts(
    client: &reqwest::Client,
    base: &str,
    package_path: &Path,
    part_size: u64,
    created: &MultipartCreated,
) -> Result<Vec<MultipartPartAck>, UploadFailure> {
    use tokio::io::AsyncReadExt;

    let mut file = tokio::fs::File::open(package_path)
        .await
        .map_err(|error| UploadFailure::transient(format!("Could not open the encrypted package: {error}")))?;

    let mut parts: Vec<MultipartPartAck> = Vec::new();
    let mut part_number: u32 = 1;
    let mut buffer = vec![0u8; part_size as usize];

    loop {
        // read() can return short reads on a normal file, so fill the buffer
        // deliberately: a short part that is not the last one would be
        // rejected by R2, which requires equal-sized parts.
        let mut filled = 0usize;
        while filled < buffer.len() {
            let read = file
                .read(&mut buffer[filled..])
                .await
                .map_err(|error| UploadFailure::transient(format!("Could not read the encrypted package: {error}")))?;
            if read == 0 {
                break;
            }
            filled += read;
        }

        if filled == 0 {
            break;
        }

        let response = client
            .post(format!("{base}/v1/multipart/part"))
            .header("X-Mimir-App-Token", OUTBOX_APP_TOKEN)
            .header("X-Mimir-Object-Key", created.object_key.as_str())
            .header("X-Mimir-Upload-Id", created.upload_id.as_str())
            .header("X-Mimir-Part-Number", part_number.to_string())
            .header("Content-Type", "application/octet-stream")
            .body(buffer[..filled].to_vec())
            .send()
            .await
            .map_err(|error| UploadFailure::transient(format!("Could not reach the submission server: {error}")))?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            let message = summarize_server_error(status, &body);
            return Err(if is_permanent_rejection(status) {
                UploadFailure::permanent(message)
            } else {
                UploadFailure::transient(message)
            });
        }

        let ack: MultipartPartAck = response
            .json()
            .await
            .map_err(|error| UploadFailure::transient(format!("The submission server sent an unusable reply: {error}")))?;
        parts.push(ack);

        if (filled as u64) < part_size {
            break;
        }
        part_number += 1;
    }

    if parts.is_empty() {
        return Err(UploadFailure::permanent("The encrypted package is empty and cannot be sent.".to_string()));
    }

    Ok(parts)
}

/// Rejections the server will repeat verbatim on every retry.
fn is_permanent_rejection(status: reqwest::StatusCode) -> bool {
    matches!(
        status,
        reqwest::StatusCode::PAYLOAD_TOO_LARGE | reqwest::StatusCode::UNSUPPORTED_MEDIA_TYPE
    )
}

fn record_failure(entry_dir: &Path, entry: &mut OutboxEntry, error: String) -> Result<OutboxSubmitResult, ScanFailure> {
    entry.attempts += 1;
    entry.last_error = error;
    write_entry(entry_dir, entry)?;
    Ok(OutboxSubmitResult {
        package_id: entry.package_id.clone(),
        status: "pending".to_string(),
        message: "Saved locally. Mimir will retry sending automatically.".to_string(),
    })
}

/// As `record_failure`, but for something retrying cannot fix. The package is
/// still never deleted -- it stays on disk for a manual transfer.
fn record_permanent_failure(
    entry_dir: &Path,
    entry: &mut OutboxEntry,
    error: String,
) -> Result<OutboxSubmitResult, ScanFailure> {
    entry.attempts += 1;
    entry.last_error = error.clone();
    entry.permanent_failure = true;
    write_entry(entry_dir, entry)?;
    Ok(OutboxSubmitResult {
        package_id: entry.package_id.clone(),
        status: "pending".to_string(),
        message: error,
    })
}

pub fn list_entries() -> Result<Vec<OutboxEntry>, ScanFailure> {
    let root = outbox_root()?;
    if !root.exists() {
        return Ok(Vec::new());
    }
    let read_dir = std::fs::read_dir(&root)
        .map_err(|error| ScanFailure::new(format!("Could not read the Outbox folder: {error}")))?;
    let mut entries = Vec::new();
    for item in read_dir {
        let Ok(item) = item else { continue };
        let path = item.path();
        if !path.is_dir() {
            continue;
        }
        if let Ok(entry) = read_entry(&path) {
            entries.push(entry);
        }
    }
    Ok(entries)
}

/// Called on app launch: retries every entry still pending, skipping ones
/// that have already failed enough times that retrying blindly again would
/// just be noise. Those need the user to press "Retry sending" instead.
pub async fn retry_pending() -> Result<Vec<OutboxSubmitResult>, ScanFailure> {
    let root = outbox_root()?;
    let mut results = Vec::new();
    for entry in list_entries()? {
        // Skip anything already known to be unsendable: re-uploading an
        // oversized package on every launch just burns the user's bandwidth to
        // earn the same rejection.
        if entry.status != "pending" || entry.permanent_failure || entry.attempts > MAX_AUTO_RETRY_ATTEMPTS {
            continue;
        }
        let entry_dir = outbox_entry_dir(&root, &entry.package_id);
        // One unusable entry must not abort the batch. `attempt_upload` returns
        // `Err` -- not a recorded failure -- when the package file is gone or the
        // kind is unrecognized, so propagating here would let a single quarantined
        // or hand-edited entry permanently block every *other* pending submission
        // from ever retrying on launch. Record it and keep going instead.
        let result = match attempt_upload(&entry_dir).await {
            Ok(result) => result,
            Err(failure) => OutboxSubmitResult {
                package_id: entry.package_id.clone(),
                status: "blocked".to_string(),
                message: failure.message.clone(),
            },
        };
        results.push(result);
    }
    Ok(results)
}

/// A manual retry, triggered by the user -- unlike `retry_pending`, this
/// ignores the attempt cap, because a deliberate click should always be
/// allowed to try again.
pub async fn retry_one(package_id: &str) -> Result<OutboxSubmitResult, ScanFailure> {
    let root = outbox_root()?;
    let entry_dir = outbox_entry_dir(&root, package_id);
    if !entry_dir.is_dir() {
        return Err(ScanFailure::new("That Outbox entry no longer exists."));
    }
    attempt_upload(&entry_dir).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::future::Future;
    use std::net::TcpListener;
    use std::process::{Child, Command, Stdio};
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::sync::Mutex as StdMutex;
    use std::time::{Duration, Instant};

    // MIMIR_OUTBOX_DIR / MIMIR_INTAKE_URL are process-wide env vars that these
    // tests mutate, so they must run one at a time relative to each other --
    // this is the standard guard for that.
    static ENV_LOCK: StdMutex<()> = StdMutex::new(());
    static COUNTER: AtomicU64 = AtomicU64::new(0);

    fn unique_temp_dir(label: &str) -> PathBuf {
        let id = COUNTER.fetch_add(1, Ordering::Relaxed);
        std::env::temp_dir().join(format!("mimir-outbox-test-{label}-{}-{}", std::process::id(), id))
    }

    /// Runs the real `scripts/dev_intake_mock.py` as a child process, rather
    /// than a hand-rolled HTTP responder. A first attempt at this used a
    /// ~70-line raw-socket parser instead, and it was genuinely flaky under
    /// Windows even running single-threaded -- not a bug in attempt_upload,
    /// but in trusting home-grown HTTP framing over something already proven
    /// correct. This project's Rust dev workflow already assumes Python is
    /// on PATH (most commands shell out to it), so that's not a new cost.
    struct MockServer {
        child: Child,
        pub url: String,
        pub storage_dir: PathBuf,
    }

    impl Drop for MockServer {
        fn drop(&mut self) {
            let _ = self.child.kill();
            let _ = self.child.wait();
            let _ = std::fs::remove_dir_all(&self.storage_dir);
        }
    }

    fn free_port() -> u16 {
        let listener = TcpListener::bind("127.0.0.1:0").expect("find a free port");
        listener.local_addr().expect("free port addr").port()
        // Dropped here, releasing the port for the child process to bind.
        // A brief window exists where another process could grab it first;
        // acceptable for a local test against loopback-only traffic.
    }

    fn spawn_mock_server(app_token: &str) -> MockServer {
        spawn_mock_server_with_part_size(app_token, None)
    }

    fn spawn_mock_server_with_part_size(app_token: &str, part_size: Option<u64>) -> MockServer {
        let port = free_port();
        let storage_dir = unique_temp_dir("mockstore");
        std::fs::create_dir_all(&storage_dir).expect("create mock storage dir");

        let script = Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("scripts").join("dev_intake_mock.py");
        let mut command = Command::new("python");
        command
            .arg(&script)
            .arg("serve")
            .arg("--storage-dir")
            .arg(&storage_dir)
            .arg("--app-token")
            .arg(app_token)
            .arg("--port")
            .arg(port.to_string());
        if let Some(bytes) = part_size {
            command.arg("--part-size").arg(bytes.to_string());
        }
        let child = command
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("start scripts/dev_intake_mock.py -- is `python` on PATH?");

        let url = format!("http://127.0.0.1:{port}");
        wait_until_listening(port);

        MockServer { child, url, storage_dir }
    }

    fn wait_until_listening(port: u16) {
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if std::net::TcpStream::connect(("127.0.0.1", port)).is_ok() {
                return;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        panic!("dev_intake_mock.py did not start listening within 5s");
    }

    /// Sets up an isolated Outbox with one pending entry (a fake encrypted
    /// package starting with the real age magic header, so
    /// dev_intake_mock.py's content-shape check accepts it), runs `body`,
    /// then cleans up -- via a Drop guard, so cleanup still happens if an
    /// assertion inside `body` panics. Must be called with ENV_LOCK held.
    async fn with_pending_entry<F, Fut>(intake_url: &str, body: F)
    where
        F: FnOnce(PathBuf) -> Fut,
        Fut: Future<Output = ()>,
    {
        with_pending_entry_of_kind(intake_url, SubmissionKind::Feedback, body).await
    }

    /// As above, but for a chosen submission kind. Contribution and feedback
    /// take different routes and different package suffixes, and only feedback
    /// was ever covered -- while contribution is the route that has never
    /// delivered a real package in production.
    async fn with_pending_entry_of_kind<F, Fut>(intake_url: &str, kind: SubmissionKind, body: F)
    where
        F: FnOnce(PathBuf) -> Fut,
        Fut: Future<Output = ()>,
    {
        struct EnvCleanup {
            root: PathBuf,
        }
        impl Drop for EnvCleanup {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.root);
                std::env::remove_var("MIMIR_OUTBOX_DIR");
                std::env::remove_var("MIMIR_INTAKE_URL");
            }
        }

        let root = unique_temp_dir("root");
        std::fs::create_dir_all(&root).expect("create test outbox root");
        std::env::set_var("MIMIR_OUTBOX_DIR", &root);
        std::env::set_var("MIMIR_INTAKE_URL", intake_url);
        let _cleanup = EnvCleanup { root: root.clone() };

        let package_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        let entry_dir = outbox_entry_dir(&root, package_id);
        std::fs::create_dir_all(&entry_dir).expect("create entry dir");
        std::fs::write(
            outbox_package_path(&entry_dir, kind),
            b"age-encryption.org/v1\nfake payload, just needs the real header",
        )
        .expect("write fake package");
        stage_pending_entry(&entry_dir, kind, package_id).expect("stage pending entry");

        body(entry_dir).await;
    }

    #[tokio::test]
    async fn successful_upload_marks_the_entry_sent() {
        let _guard = ENV_LOCK.lock().unwrap();
        let server = spawn_mock_server(OUTBOX_APP_TOKEN);

        with_pending_entry(&server.url, |entry_dir| async move {
            let result = attempt_upload(&entry_dir).await.unwrap();
            assert_eq!(result.status, "sent");

            let reloaded = read_entry(&entry_dir).unwrap();
            assert_eq!(reloaded.status, "sent");
            assert_eq!(reloaded.attempts, 0, "a successful first attempt should not count as a failure");
        })
        .await;
    }

    #[tokio::test]
    async fn server_rejection_leaves_the_entry_pending_and_records_the_error() {
        let _guard = ENV_LOCK.lock().unwrap();
        // A real rejection, not a scripted one: the server is started with a
        // different app token than attempt_upload actually sends
        // (OUTBOX_APP_TOKEN is a fixed constant, not something a test can
        // override), so it genuinely returns 401 bad_token.
        let server = spawn_mock_server("a-different-token-than-the-app-uses");

        with_pending_entry(&server.url, |entry_dir| async move {
            let result = attempt_upload(&entry_dir).await.unwrap();
            assert_eq!(result.status, "pending");

            let reloaded = read_entry(&entry_dir).unwrap();
            assert_eq!(reloaded.status, "pending");
            assert_eq!(reloaded.attempts, 1);
            assert!(
                reloaded.last_error.contains("bad_token"),
                "the real rejection reason must survive, not be silently dropped: {}",
                reloaded.last_error
            );
            // The package itself must never be deleted on failure.
            assert!(outbox_package_path(&entry_dir, SubmissionKind::Feedback).is_file());
        })
        .await;
    }

    #[tokio::test]
    async fn connection_failure_leaves_the_entry_pending_without_losing_the_package() {
        let _guard = ENV_LOCK.lock().unwrap();
        // Nothing is listening on this port; the connection is refused
        // immediately rather than timing out, keeping the test fast.
        with_pending_entry("http://127.0.0.1:1", |entry_dir| async move {
            let result = attempt_upload(&entry_dir).await.unwrap();
            assert_eq!(result.status, "pending");

            let reloaded = read_entry(&entry_dir).unwrap();
            assert_eq!(reloaded.attempts, 1);
            assert!(outbox_package_path(&entry_dir, SubmissionKind::Feedback).is_file());
        })
        .await;
    }

    #[tokio::test]
    async fn a_sent_entry_is_not_re_uploaded() {
        let _guard = ENV_LOCK.lock().unwrap();
        // No server at all -- if attempt_upload tried to send again, this
        // would fail with a connection error instead of short-circuiting.
        with_pending_entry("http://127.0.0.1:1", |entry_dir| async move {
            let mut entry = read_entry(&entry_dir).unwrap();
            entry.status = "sent".to_string();
            write_entry(&entry_dir, &entry).unwrap();

            let result = attempt_upload(&entry_dir).await.unwrap();
            assert_eq!(result.status, "sent");
        })
        .await;
    }

    #[tokio::test]
    async fn retry_pending_skips_entries_past_the_auto_retry_cap() {
        let _guard = ENV_LOCK.lock().unwrap();
        with_pending_entry("http://127.0.0.1:1", |entry_dir| async move {
            let mut entry = read_entry(&entry_dir).unwrap();
            entry.attempts = MAX_AUTO_RETRY_ATTEMPTS + 1;
            write_entry(&entry_dir, &entry).unwrap();

            let results = retry_pending().await.unwrap();
            assert!(
                results.is_empty(),
                "an entry past the auto-retry cap must be left for a manual retry, not swept automatically"
            );

            // attempts must be unchanged -- retry_pending should not have
            // touched an entry it skipped.
            let reloaded = read_entry(&entry_dir).unwrap();
            assert_eq!(reloaded.attempts, MAX_AUTO_RETRY_ATTEMPTS + 1);
        })
        .await;
    }

    #[tokio::test]
    async fn one_unusable_entry_does_not_block_the_rest_of_the_batch() {
        let _guard = ENV_LOCK.lock().unwrap();
        let server = spawn_mock_server(OUTBOX_APP_TOKEN);

        with_pending_entry(&server.url, |good_entry_dir| async move {
            // A second entry staged as pending but with no encrypted package
            // on disk -- exactly what an antivirus quarantine leaves behind.
            // Its id sorts before the healthy entry's so it is reached first
            // on any sane directory ordering.
            let root = outbox_root().unwrap();
            let broken_id = "00000000000000000000000000000000";
            let broken_dir = outbox_entry_dir(&root, broken_id);
            std::fs::create_dir_all(&broken_dir).expect("create broken entry dir");
            stage_pending_entry(&broken_dir, SubmissionKind::Feedback, broken_id)
                .expect("stage broken entry");

            // This previously propagated the broken entry's error with `?`, so
            // a single quarantined package permanently stopped every *other*
            // pending submission from ever being retried on launch.
            let results = retry_pending()
                .await
                .expect("one unusable entry must not fail the whole batch");

            assert_eq!(results.len(), 2, "both entries should be accounted for");
            assert!(
                results
                    .iter()
                    .any(|result| result.package_id == broken_id && result.status == "blocked"),
                "the unusable entry should be reported as blocked rather than aborting the run"
            );
            assert_eq!(
                read_entry(&good_entry_dir).unwrap().status,
                "sent",
                "the healthy entry must still have been uploaded"
            );
        })
        .await;
    }

    #[tokio::test]
    async fn manual_retry_ignores_the_auto_retry_cap() {
        let _guard = ENV_LOCK.lock().unwrap();
        let server = spawn_mock_server(OUTBOX_APP_TOKEN);
        with_pending_entry(&server.url, |entry_dir| async move {
            let mut entry = read_entry(&entry_dir).unwrap();
            entry.attempts = MAX_AUTO_RETRY_ATTEMPTS + 10;
            write_entry(&entry_dir, &entry).unwrap();
            let package_id = entry.package_id.clone();

            // A deliberate user click must always be allowed to try again,
            // regardless of how many times auto-retry has already failed.
            let result = retry_one(&package_id).await.unwrap();
            assert_eq!(result.status, "sent");
        })
        .await;
    }

    #[tokio::test]
    async fn a_successful_upload_is_visible_in_the_real_servers_storage() {
        // The strongest check: not just that attempt_upload reports "sent",
        // but that the object genuinely landed in the mock server's storage
        // at the key format the real Worker will use too.
        let _guard = ENV_LOCK.lock().unwrap();
        let server = spawn_mock_server(OUTBOX_APP_TOKEN);
        let storage_dir = server.storage_dir.clone();

        with_pending_entry(&server.url, |entry_dir| async move {
            let result = attempt_upload(&entry_dir).await.unwrap();
            assert_eq!(result.status, "sent");
        })
        .await;

        let stored = std::fs::read_dir(storage_dir.join("feedback"))
            .and_then(|entries| entries.collect::<std::io::Result<Vec<_>>>())
            .map(|entries| !entries.is_empty())
            .unwrap_or(false);
        assert!(stored, "the mock server's storage should contain a feedback/<year> folder after a successful send");
    }

    #[test]
    fn server_errors_never_carry_markup_to_the_user() {
        // A real 413 from Cloudflare's edge is a full HTML page. Pasting it in
        // raw put "<html>" in front of the user and in the entry's last_error.
        let html = summarize_server_error(
            reqwest::StatusCode::PAYLOAD_TOO_LARGE,
            "<html>\n<head><title>413 Request Entity Too Large</title></head>\n<body>...</body>\n</html>",
        );
        assert!(!html.contains('<'), "markup must not reach the user: {html}");
        assert!(html.contains("too large"), "the actionable part must survive: {html}");
        assert!(html.contains("not help"), "a 413 is not worth retrying, and should say so: {html}");

        // A real JSON reason from the Worker is worth keeping verbatim.
        let json = summarize_server_error(
            reqwest::StatusCode::UNAUTHORIZED,
            "{\"accepted\":false,\"reason\":\"bad_token\"}",
        );
        assert!(json.contains("bad_token"), "the Worker's own reason must survive: {json}");

        // An empty body still produces a sentence rather than a dangling colon.
        let empty = summarize_server_error(reqwest::StatusCode::BAD_GATEWAY, "   ");
        assert!(!empty.trim_end().ends_with(':'), "should not trail an empty reason: {empty}");
    }

    #[test]
    fn long_server_errors_are_clipped() {
        let long = summarize_server_error(reqwest::StatusCode::BAD_REQUEST, &"x".repeat(5000));
        assert!(long.len() < 400, "an unbounded body must not land in the entry file");
    }

    #[tokio::test]
    async fn an_oversized_package_is_refused_before_any_upload_is_attempted() {
        let _guard = ENV_LOCK.lock().unwrap();
        // Nothing is listening here: if the size guard did not fire first, this
        // would fail with a connection error instead of the size message.
        // Cap lowered so "oversized" is a few kilobytes rather than 2 GB.
        std::env::set_var("MIMIR_MAX_SUBMISSION_BYTES", "4096");

        with_pending_entry_of_kind("http://127.0.0.1:1", SubmissionKind::Contribution, |entry_dir| async move {
            let package = outbox_package_path(&entry_dir, SubmissionKind::Contribution);
            let oversized = vec![b'x'; 4097];
            std::fs::write(&package, &oversized).expect("write oversized package");

            let result = attempt_upload(&entry_dir).await.unwrap();
            assert_eq!(result.status, "pending");

            let reloaded = read_entry(&entry_dir).unwrap();
            assert!(
                reloaded.last_error.contains("too large") || reloaded.last_error.contains("refuses"),
                "the size limit should be named, not a connection error: {}",
                reloaded.last_error
            );
            assert!(
                reloaded.last_error.contains("not help"),
                "retrying an oversized package is futile and must say so: {}",
                reloaded.last_error
            );
            assert!(reloaded.permanent_failure, "an oversized package must be flagged unsendable");
            // The package is still kept, exactly as with any other failure.
            assert!(package.is_file());

            // And auto-retry must leave it alone rather than re-uploading it
            // on every launch.
            let swept = retry_pending().await.unwrap();
            assert!(
                swept.is_empty(),
                "auto-retry should skip a package it already knows cannot be sent"
            );
        })
        .await;

        std::env::remove_var("MIMIR_MAX_SUBMISSION_BYTES");
    }

    #[tokio::test]
    async fn a_package_larger_than_one_part_is_uploaded_in_chunks_and_reassembled() {
        // The whole reason the chunked path exists: Cloudflare refuses a body
        // over its edge limit before the Worker runs, so a contribution
        // carrying raw video could never arrive in one request. Part size is
        // shrunk on both ends so this exercises three real parts without a
        // 64 MB fixture.
        let _guard = ENV_LOCK.lock().unwrap();
        let part_bytes: u64 = 64 * 1024;
        let server = spawn_mock_server_with_part_size(OUTBOX_APP_TOKEN, Some(part_bytes));
        let storage_dir = server.storage_dir.clone();
        std::env::set_var("MIMIR_UPLOAD_PART_SIZE", part_bytes.to_string());

        // Two and a half parts, so the final short part is covered too.
        let mut payload = b"age-encryption.org/v1\n".to_vec();
        payload.resize((part_bytes * 2 + part_bytes / 2) as usize, b'M');
        let expected = payload.clone();

        with_pending_entry_of_kind(&server.url, SubmissionKind::Contribution, |entry_dir| async move {
            let package = outbox_package_path(&entry_dir, SubmissionKind::Contribution);
            std::fs::write(&package, &payload).expect("write multi-part package");

            let result = attempt_upload(&entry_dir).await.unwrap();
            assert_eq!(result.status, "sent", "chunked upload should succeed: {}", result.message);

            let reloaded = read_entry(&entry_dir).unwrap();
            assert_eq!(reloaded.status, "sent");
            assert!(!reloaded.permanent_failure);
            assert!(package.is_file(), "the encrypted package is kept after sending");
        })
        .await;

        std::env::remove_var("MIMIR_UPLOAD_PART_SIZE");

        // The strongest check: the reassembled object is byte-identical, so
        // the parts went up in the right order and none was truncated.
        let stored = std::fs::read_dir(storage_dir.join("contributions"))
            .expect("contributions prefix should exist")
            .filter_map(|entry| entry.ok())
            .flat_map(|year| std::fs::read_dir(year.path()).into_iter().flatten().filter_map(|m| m.ok()))
            .flat_map(|month| std::fs::read_dir(month.path()).into_iter().flatten().filter_map(|f| f.ok()))
            .map(|file| file.path())
            .find(|path| path.is_file())
            .expect("a reassembled object should exist");

        let contents = std::fs::read(&stored).expect("read reassembled object");
        assert_eq!(contents.len(), expected.len(), "reassembled size must match");
        assert_eq!(contents, expected, "reassembled bytes must match exactly");

        // Proof the chunked route was actually taken, not the single-shot one:
        // the mock only creates its staging root when a multipart upload is
        // opened. Without this the test would pass either way, since the
        // single-shot route would happily accept a payload this small.
        assert!(
            storage_dir.join(".uploads").is_dir(),
            "the multipart route should have been used, not the single-shot one"
        );
    }

    #[tokio::test]
    async fn a_service_without_chunked_upload_says_so_rather_than_reporting_404() {
        // Deploy ordering: if a client with chunking reaches an intake service
        // that predates the multipart routes, create returns 404. A bare
        // "404 Not Found" reads like a bug in Mimir rather than a service that
        // has not been updated yet.
        let _guard = ENV_LOCK.lock().unwrap();
        let part_bytes: u64 = 32 * 1024;
        // The mock is told nothing about multipart, but the single-shot routes
        // it does serve return 404 for unknown paths -- exactly the old
        // Worker's behaviour.
        let server = spawn_mock_server(OUTBOX_APP_TOKEN);
        std::env::set_var("MIMIR_UPLOAD_PART_SIZE", part_bytes.to_string());
        // Point the multipart calls at a path the mock does not implement by
        // using a base URL whose /v1/multipart/* routes 404.
        let base = format!("{}/nope", server.url);
        std::env::set_var("MIMIR_INTAKE_URL", &base);

        let mut payload = b"age-encryption.org/v1\n".to_vec();
        payload.resize((part_bytes * 2) as usize, b'M');

        with_pending_entry_of_kind(&base, SubmissionKind::Contribution, |entry_dir| async move {
            let package = outbox_package_path(&entry_dir, SubmissionKind::Contribution);
            std::fs::write(&package, &payload).expect("write package");

            let result = attempt_upload(&entry_dir).await.unwrap();
            assert_eq!(result.status, "pending");

            let reloaded = read_entry(&entry_dir).unwrap();
            assert!(
                reloaded.last_error.contains("chunked uploads"),
                "should name the missing capability, not just the status: {}",
                reloaded.last_error
            );
            assert!(
                !reloaded.permanent_failure,
                "a service that has not been updated yet is a transient condition"
            );
        })
        .await;

        std::env::remove_var("MIMIR_UPLOAD_PART_SIZE");
    }

    #[tokio::test]
    async fn the_service_refuses_a_part_larger_than_the_agreed_size() {
        // total_bytes is validated once at create and never seen again, so the
        // real ceiling on an upload is MAX_PARTS multiplied by the largest part
        // the service will accept. Without a per-part cap a client could
        // declare a small total and then push far more, and the app token is
        // extractable from the binary, so that is reachable by anyone.
        //
        // Driven over raw HTTP rather than through attempt_upload on purpose:
        // the Rust client takes part_size from the create response, so a
        // well-behaved client physically cannot send an oversized part. This
        // guard exists for a client that is not well-behaved, and that is the
        // only way to exercise it.
        let _guard = ENV_LOCK.lock().unwrap();
        let part_bytes: u64 = 32 * 1024;
        let server = spawn_mock_server_with_part_size(OUTBOX_APP_TOKEN, Some(part_bytes));
        let client = reqwest::Client::new();

        let created: serde_json::Value = client
            .post(format!("{}/v1/multipart/create", server.url))
            .header("X-Mimir-App-Token", OUTBOX_APP_TOKEN)
            .json(&json!({
                "kind": "contribution",
                "package_id": "cccccccccccccccccccccccccccccccc",
                "total_bytes": part_bytes,
            }))
            .send()
            .await
            .expect("create should reach the mock")
            .json()
            .await
            .expect("create should return json");

        let oversized = vec![b'M'; (part_bytes * 3) as usize];
        let response = client
            .post(format!("{}/v1/multipart/part", server.url))
            .header("X-Mimir-App-Token", OUTBOX_APP_TOKEN)
            .header("X-Mimir-Object-Key", created["object_key"].as_str().unwrap())
            .header("X-Mimir-Upload-Id", created["upload_id"].as_str().unwrap())
            .header("X-Mimir-Part-Number", "1")
            .body(oversized)
            .send()
            .await
            .expect("part should reach the mock");

        assert_eq!(
            response.status(),
            reqwest::StatusCode::PAYLOAD_TOO_LARGE,
            "a part above the agreed size must be refused"
        );
        let body = response.text().await.unwrap_or_default();
        assert!(body.contains("part_too_large"), "should name the reason: {body}");
    }

    #[tokio::test]
    async fn the_service_refuses_to_complete_an_upload_with_no_first_part() {
        // The age-magic shape check only runs on part 1, so completing without
        // one would skip it entirely.
        let _guard = ENV_LOCK.lock().unwrap();
        let server = spawn_mock_server_with_part_size(OUTBOX_APP_TOKEN, Some(32 * 1024));
        let client = reqwest::Client::new();

        let created: serde_json::Value = client
            .post(format!("{}/v1/multipart/create", server.url))
            .header("X-Mimir-App-Token", OUTBOX_APP_TOKEN)
            .json(&json!({
                "kind": "contribution",
                "package_id": "dddddddddddddddddddddddddddddddd",
                "total_bytes": 1024,
            }))
            .send()
            .await
            .expect("create should reach the mock")
            .json()
            .await
            .expect("create should return json");

        let response = client
            .post(format!("{}/v1/multipart/complete", server.url))
            .header("X-Mimir-App-Token", OUTBOX_APP_TOKEN)
            .json(&json!({
                "object_key": created["object_key"],
                "upload_id": created["upload_id"],
                "parts": [{ "part_number": 2, "etag": "whatever" }],
            }))
            .send()
            .await
            .expect("complete should reach the mock");

        assert_eq!(response.status(), reqwest::StatusCode::BAD_REQUEST);
        let body = response.text().await.unwrap_or_default();
        assert!(body.contains("missing_first_part"), "should name the reason: {body}");
    }

    #[tokio::test]
    async fn a_contribution_reaches_the_contribution_route_and_storage() {
        // Every other test here uses Feedback, so the contribution route had no
        // coverage at all -- and contribution is the one that has never
        // delivered a real package in production (the intake sync log shows a
        // single synthetic object against 22 real feedback packages). A
        // contribution takes a different route and a different package suffix,
        // so "feedback works" was never evidence that this does.
        let _guard = ENV_LOCK.lock().unwrap();
        let server = spawn_mock_server(OUTBOX_APP_TOKEN);
        let storage_dir = server.storage_dir.clone();

        with_pending_entry_of_kind(&server.url, SubmissionKind::Contribution, |entry_dir| async move {
            let result = attempt_upload(&entry_dir).await.unwrap();
            assert_eq!(result.status, "sent");

            let reloaded = read_entry(&entry_dir).unwrap();
            assert_eq!(reloaded.kind, "contribution");
            assert_eq!(reloaded.status, "sent");
            // The encrypted package survives a successful send, same as feedback.
            assert!(outbox_package_path(&entry_dir, SubmissionKind::Contribution).is_file());
        })
        .await;

        let stored = std::fs::read_dir(storage_dir.join("contributions"))
            .and_then(|entries| entries.collect::<std::io::Result<Vec<_>>>())
            .map(|entries| !entries.is_empty())
            .unwrap_or(false);
        assert!(
            stored,
            "a contribution must land under contributions/, not feedback/ -- the two are filed separately at intake",
        );
    }
}

// One-shot verification against the real production Worker was run by hand
// here (2026-07-31) and then removed, on purpose: a permanent #[ignore]d
// test that hits real infrastructure risks someone running
// `cargo test -- --ignored` and silently writing a junk object into the
// production bucket. It confirmed the actual attempt_upload code path,
// unmodified, correctly sends to https://mimir-ingest.mimir-ingest-a4f9c1.workers.dev
// and receives "sent" back -- stronger evidence than the curl checks
// already run, which only proved the Worker's contract in isolation, not
// that this Rust code speaks it correctly.
