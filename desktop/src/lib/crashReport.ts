import { invoke } from '@tauri-apps/api/core'
import { MIMIR_VERSION } from '../config'

/**
 * Sending a crash report, through the road feedback already travels.
 *
 * Crashes have always been written down -- Documents\Mimir Logs\app_crash_log.txt,
 * rotated, with the error and stack trace -- and have never once reached
 * anybody, because seeing one meant asking a tester to find a file in Documents
 * and email it. So in practice the app has been crashing invisibly.
 *
 * Deliberately not Sentry. The submission pipeline already exists and is
 * proven: encrypted on this machine with age, queued in the Outbox, retried,
 * uploaded, and listed in Forge beside feedback. Adding a hosted error service
 * would mean an account, a dependency, a data-residency decision and a change
 * to the privacy notice, to arrive at something this already does.
 *
 * A report carries the log tail, the app version and what the user was doing.
 * It carries no video. Local paths are stripped at the packaging boundary by
 * feedback_package.py, which matters more here than for feedback because a
 * stack trace is made of paths.
 */

const MAX_LOG_BYTES = 32 * 1024

export interface CrashReportResult {
  status: string
  message: string
}

/** The recent crash log, or empty when nothing has gone wrong. */
export async function readCrashLog(): Promise<string> {
  try {
    return await invoke<string>('read_recent_crash_log', { maxBytes: MAX_LOG_BYTES })
  } catch {
    return ''
  }
}

export async function sendCrashReport(log: string, note: string): Promise<CrashReportResult> {
  const report = {
    // Shaped like feedback so the whole pipeline -- packaging, encryption,
    // Outbox, Worker, Forge -- handles it without knowing it is different.
    incident_id: 'crash-report',
    kind: 'crash_report',
    current_severity: '',
    user_selected_feedback: 'Crash report',
    notes: note.trim(),
    app_version: MIMIR_VERSION,
    platform: navigator.userAgent,
    crash_log: log,
    timestamp: new Date().toISOString(),
  }

  const saved = await invoke<{ feedback_file: string }>('save_incident_feedback', {
    feedback: report,
    includeVideo: false,
    videoPath: null,
  })

  return invoke<CrashReportResult>('submit_incident_feedback', {
    feedbackJsonPath: saved.feedback_file,
    videoPath: null,
    attemptSend: true,
  })
}

/** How many crashes the log holds, for telling someone what they would send. */
export function countCrashes(log: string): number {
  if (!log.trim()) {
    return 0
  }
  return log.split('\n').filter(line => line.trim() === '---').length || 1
}
