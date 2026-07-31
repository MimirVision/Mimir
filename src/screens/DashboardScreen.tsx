import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { describeError, type DescribedError } from '../lib/errorMessages'
import { ErrorNotice } from '../components/ErrorNotice'
import type { GateProgress, SyncOutcome } from '../lib/types'

interface ProgressBarProps {
  label: string
  done: number
  target: number
}

function ProgressBar({ label, done, target }: ProgressBarProps) {
  const pct = target > 0 ? Math.min(100, Math.round((done / target) * 100)) : 0
  const met = done >= target
  return (
    <div>
      <div className="flex items-baseline justify-between text-[12px]">
        <span className="text-mimir-text-muted">{label}</span>
        <span className={met ? 'text-mimir-green' : 'text-mimir-text'}>
          {done} / {target}
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
        <div
          className={`h-full rounded-full ${met ? 'bg-mimir-green' : 'bg-mimir-accent'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

interface DashboardScreenProps {
  ready: boolean
}

export function DashboardScreen({ ready }: DashboardScreenProps) {
  const [progress, setProgress] = useState<GateProgress | null>(null)
  const [loadError, setLoadError] = useState<DescribedError | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [syncError, setSyncError] = useState<DescribedError | null>(null)
  const [lastSync, setLastSync] = useState<SyncOutcome | null>(null)

  const loadStatus = async () => {
    if (!ready) return
    try {
      const result = await api.getStatus()
      setProgress(result)
      setLoadError(null)
    } catch (err) {
      setLoadError(describeError(err, 'Could not load progress.'))
    }
  }

  useEffect(() => {
    void loadStatus()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready])

  const sync = async () => {
    setSyncing(true)
    setSyncError(null)
    try {
      const outcome = await api.runSync()
      setLastSync(outcome)
      setProgress(outcome.result.gate_progress)
    } catch (err) {
      setSyncError(describeError(err, 'Sync failed.'))
    } finally {
      setSyncing(false)
    }
  }

  if (!ready) {
    return (
      <div className="mx-auto max-w-2xl px-6 py-8">
        <p className="text-[13px] text-mimir-text-muted">
          Set your dataset root, inboxes, and identity file in Settings before syncing.
        </p>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-2xl px-6 py-8">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-medium text-mimir-text">Dashboard</h1>
        <button
          type="button"
          disabled={syncing}
          onClick={sync}
          className="rounded-md border border-mimir-accent/40 bg-mimir-accent-soft px-4 py-2 text-[12px] font-medium text-mimir-accent disabled:opacity-40"
        >
          {syncing ? 'Syncing...' : 'Sync now'}
        </button>
      </div>

      <ErrorNotice error={loadError} className="mt-4" />
      <ErrorNotice error={syncError} className="mt-4" />

      {progress && (
        <div className="mt-6 rounded-lg border border-mimir-border bg-mimir-surface-soft/60 p-4">
          <div className="flex items-center justify-between">
            <span className="text-[12px] font-medium text-mimir-text">Pilot gate progress</span>
            {progress.pilot_gate_met && (
              <span className="rounded-full bg-mimir-accent-soft px-2 py-0.5 text-[10px] font-medium text-mimir-accent">
                Gate met
              </span>
            )}
          </div>
          <div className="mt-3 space-y-3">
            <ProgressBar label="Complete groups" done={progress.current.groups} target={progress.targets.groups} />
            <ProgressBar label="Positives" done={progress.current.positives} target={progress.targets.positives} />
            <ProgressBar
              label="Hard negatives"
              done={progress.current.hard_negatives}
              target={progress.targets.hard_negatives}
            />
          </div>
          <div className="mt-3 text-[11px] text-mimir-text-subtle">
            {progress.collections} collection(s), {progress.items} item(s) total. Blind re-labels:{' '}
            {progress.blind_relabels} / {progress.blind_relabels_required} required.
          </div>
          {progress.audit_errors.length > 0 && (
            <div className="mt-2 text-[11px] text-mimir-red">{progress.audit_errors.length} audit error(s).</div>
          )}
        </div>
      )}

      {lastSync && (
        <div className="mt-6 rounded-lg border border-mimir-border bg-mimir-surface-soft/60 p-4">
          <div className="text-[12px] font-medium text-mimir-text">Last sync</div>
          <div className="mt-1 text-[11px] text-mimir-text-subtle">
            {lastSync.result.new_contribution_count} new contribution(s), {lastSync.result.new_feedback_count} new
            feedback item(s).
          </div>
          {lastSync.progress_log.trim() && (
            <details className="mt-2.5 rounded-md border border-mimir-border bg-black/20 p-2.5">
              <summary className="cursor-pointer text-[11px] font-medium text-mimir-text-muted">
                Sync log
              </summary>
              <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-mimir-text-subtle">
                {lastSync.progress_log}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  )
}
