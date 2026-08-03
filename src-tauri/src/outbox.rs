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
// no-read-access) against C:\Mimir_Ingest_Worker on 2026-07-31. Point
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
            record_failure(entry_dir, &mut entry, format!("The submission server declined this ({status}): {reason}"))
        }
        Err(error) => record_failure(
            entry_dir,
            &mut entry,
            format!("Could not reach the submission server: {error}"),
        ),
    }
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
        if entry.status != "pending" || entry.attempts > MAX_AUTO_RETRY_ATTEMPTS {
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
        let port = free_port();
        let storage_dir = unique_temp_dir("mockstore");
        std::fs::create_dir_all(&storage_dir).expect("create mock storage dir");

        let script = Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("scripts").join("dev_intake_mock.py");
        let child = Command::new("python")
            .arg(&script)
            .arg("serve")
            .arg("--storage-dir")
            .arg(&storage_dir)
            .arg("--app-token")
            .arg(app_token)
            .arg("--port")
            .arg(port.to_string())
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
