import { test, expect } from '@playwright/test'

const SESSION = {
  schema_version: 'mimir_v2', generated_at: '2026-07-30T12:00:00Z',
  output_path: 'C:/MimirOutput', session_archive_path: 'C:/MimirOutput/sessions/session.json',
  source_path: 'C:/Footage', scan_mode: 'thorough', ai_enabled: false,
  incidents: [{
    id: 'incident_0001', event_id: 1, severity: 'IMPORTANT', final_severity: 'IMPORTANT',
    source_filename: 'clip.mp4', source_stem: 'clip', event_type: 'sentry_event',
    primary_key_moment_sec: 4, summary: 'Contact with the parked vehicle.',
    camera_clips: [{ camera: 'front', path: 'C:/Footage/front.mp4' }],
  }],
  scan_summary: { clips_scanned: 1, incidents: 1 },
}

test('correcting a verdict asks once, then sends', async ({ page }) => {
  const calls: string[] = []
  await page.exposeFunction('__record', (name: string) => { calls.push(name) })
  await page.addInitScript(({ session }) => {
    window.localStorage.setItem('mimir_onboarding_completed', 'true')
    window.localStorage.setItem('mimir_beta_privacy_notice_accepted', 'true')
    const responses: Record<string, unknown> = {
      load_latest_session_json: JSON.stringify(session), list_session_history: [],
      check_system_requirements: { ok: true, checked_at: 'x', items: [] },
      check_local_ai: { ok: false, ollama_available: false, model_installed: false },
      list_outbox_entries: [], retry_pending_outbox: { retried: 0 },
      save_manual_status: null,
      save_incident_feedback: { feedback_file: 'C:/f/feedback.json' },
      submit_incident_feedback: { package_id: 'p1', status: 'sent', message: 'Sent.' },
    }
    // @ts-expect-error stub
    window.__TAURI_INTERNALS__ = {
      invoke: (c: string) => { (window as any).__record(c); return Promise.resolve(responses[c] ?? null) },
      transformCallback: (cb: unknown) => cb,
      convertFileSrc: (p: string) => `asset://${p}`,
    }
    // @ts-expect-error stub
    window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener: () => Promise.resolve() }
  }, { session: SESSION })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: 'Back to latest session' }).click()
  await page.locator('article button').first().click()
  await page.getByRole('button', { name: 'Back to Library' }).waitFor()

  // Nothing is sent before consent, and the prompt appears.
  await page.getByRole('button', { name: /Ignored/ }).click()
  await expect(page.getByRole('dialog', { name: 'Send your corrections to Mimir?' })).toBeVisible()
  expect(calls.filter(c => c === 'submit_incident_feedback')).toHaveLength(0)

  await page.getByRole('button', { name: 'Send corrections' }).click()
  await expect.poll(() => calls.filter(c => c === 'submit_incident_feedback').length).toBe(1)

  // Second disagreement sends with no prompt.
  await page.getByRole('button', { name: /Review/ }).first().click()
  await expect.poll(() => calls.filter(c => c === 'submit_incident_feedback').length).toBe(2)
  await expect(page.getByRole('dialog', { name: 'Send your corrections to Mimir?' })).toHaveCount(0)

  // Agreeing with Mimir is saved locally and not sent. The incident is
  // IMPORTANT, so pressing Important is agreement.
  await page.getByRole('button', { name: /Important/ }).first().click()
  await expect.poll(() => calls.filter(c => c === 'save_manual_status').length).toBeGreaterThan(0)
  await new Promise(resolve => setTimeout(resolve, 400))
  expect(calls.filter(c => c === 'submit_incident_feedback')).toHaveLength(2)
})
