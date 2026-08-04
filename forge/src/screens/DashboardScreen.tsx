import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { describeError, type DescribedError } from '../lib/errorMessages'
import { explainSyncFailure } from '../lib/syncErrors'
import { ErrorNotice } from '../components/ErrorNotice'
import { Spinner } from '../components/Spinner'
import type {
  FeedbackListItem,
  FeedbackReview,
  GateProgress,
  ReportSummary,
  SyncItemResult,
  SyncOutcome,
} from '../lib/types'

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
  onReviewFeedback: (packageId?: string) => void
}

interface NeedsReviewProps {
  items: FeedbackListItem[]
  reviews: Record<string, FeedbackReview>
  onReviewFeedback: (packageId?: string) => void
}

function NeedsReview({ items, reviews, onReviewFeedback }: NeedsReviewProps) {
  const unreviewed = items.filter(item => !reviews[item.package_id]?.reviewed)
  if (unreviewed.length === 0) return null

  const preview = unreviewed.slice(0, 5)
  return (
    <div className="mt-6 rounded-lg border border-mimir-border bg-mimir-surface-soft/60 p-4">
      <div className="flex items-center justify-between">
        <span className="text-[12px] font-medium text-mimir-text">Needs review</span>
        <span className="rounded-full bg-mimir-accent-soft px-2 py-0.5 text-[10px] font-medium text-mimir-accent">
          {unreviewed.length}
        </span>
      </div>
      <div className="mt-2.5 space-y-1.5">
        {preview.map(item => {
          const choice = item.feedback.user_selected_feedback ?? '?'
          const timestamp = item.feedback.timestamp ?? item.feedback.saved_at ?? ''
          return (
            <button
              key={item.package_id}
              type="button"
              onClick={() => onReviewFeedback(item.package_id)}
              className="flex w-full items-center justify-between rounded-md border border-mimir-border bg-mimir-bg-depth px-2.5 py-1.5 text-left text-[11px] text-mimir-text-muted hover:text-mimir-text"
            >
              <span className="font-medium text-mimir-text">{choice}</span>
              <span className="text-mimir-text-subtle">{String(timestamp).slice(0, 10)}</span>
            </button>
          )
        })}
      </div>
      {unreviewed.length > preview.length && (
        <p className="mt-2 text-[10px] text-mimir-text-subtle">
          +{unreviewed.length - preview.length} more
        </p>
      )}
      <button
        type="button"
        onClick={() => onReviewFeedback()}
        className="mt-3 w-full rounded-md border border-mimir-accent/40 bg-mimir-accent-soft px-3 py-1.5 text-[11px] font-medium text-mimir-accent"
      >
        Review now
      </button>
    </div>
  )
}

interface RecentReportsProps {
  reports: ReportSummary[]
}

function RecentReports({ reports }: RecentReportsProps) {
  if (reports.length === 0) return null

  const open = async (path: string) => {
    try {
      await api.openReportFile(path)
    } catch {
      // Opening in Explorer is a convenience; nothing else depends on it succeeding.
    }
  }

  return (
    <div className="mt-6 rounded-lg border border-mimir-border bg-mimir-surface-soft/60 p-4">
      <span className="text-[12px] font-medium text-mimir-text">Recent reports</span>
      <div className="mt-2.5 space-y-1.5">
        {reports.map(report => (
          <button
            key={report.path}
            type="button"
            onClick={() => open(report.path)}
            className="flex w-full items-center justify-between rounded-md border border-mimir-border bg-mimir-bg-depth px-2.5 py-1.5 text-left text-[11px] text-mimir-text-muted hover:text-mimir-text"
          >
            <span className="font-medium text-mimir-text">{report.filename}</span>
            <span className="text-mimir-text-subtle">
              {new Date(report.modified_unix * 1000).toLocaleDateString()}
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}

function TrainingCommands({ datasetRoot }: { datasetRoot: string }) {
  const preparedDir = `${datasetRoot}\\prepared`
  const commands = [
    `python mimir_core_v2_training.py prepare --dataset-root "${datasetRoot}" --output "${preparedDir}"`,
    `python mimir_core_v2_training.py train --prepared "${preparedDir}"`,
  ]
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(commands.join('\n'))
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard access can be denied by the OS; the commands are still
      // visible below to copy by hand, so this is not worth surfacing as
      // an error notice.
    }
  }

  return (
    <div className="mt-6 rounded-lg border border-mimir-border bg-mimir-surface-soft/60 p-4">
      <div className="flex items-center justify-between">
        <span className="text-[12px] font-medium text-mimir-text">Ready to train</span>
        <button
          type="button"
          onClick={copy}
          className="rounded-md border border-mimir-border-strong px-2.5 py-1 text-[10px] text-mimir-text"
        >
          {copied ? 'Copied' : 'Copy commands'}
        </button>
      </div>
      <p className="mt-1.5 text-[11px] leading-5 text-mimir-text-subtle">
        Syncing and reviewing here only gets the data ready -- starting a training run stays a deliberate
        step you run yourself, in C:\MimirDev\backend, so a candidate that regresses real detection can never
        reach testers automatically. These are the exact commands `train` will re-check this gate against:
      </p>
      <pre className="mt-2.5 overflow-x-auto rounded-md border border-mimir-border bg-black/25 p-2.5 text-[11px] leading-5 text-mimir-text-muted">
        {commands.join('\n')}
      </pre>
    </div>
  )
}

interface FailedItemRowProps {
  kind: 'contribution' | 'feedback'
  item: SyncItemResult
}

function FailedItemRow({ kind, item }: FailedItemRowProps) {
  const explained = explainSyncFailure(item.error ?? '')
  return (
    <div
      className={`rounded-md border p-2.5 ${
        explained.benign ? 'border-mimir-border bg-mimir-bg-depth/60' : 'border-mimir-status-red/25 bg-mimir-status-red/[0.06]'
      }`}
    >
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[11px] font-medium text-mimir-text">{item.file}</span>
        <span className="shrink-0 text-[10px] uppercase tracking-wide text-mimir-text-subtle">{kind}</span>
      </div>
      <p className={`mt-1 text-[11px] leading-5 ${explained.benign ? 'text-mimir-text-subtle' : 'text-mimir-red'}`}>
        {explained.headline}
      </p>
      {item.error && (
        <details className="mt-1.5">
          <summary className="cursor-pointer text-[10px] text-mimir-text-subtle">Raw error</summary>
          <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap text-[10px] leading-4 text-mimir-text-subtle">
            {item.error}
          </pre>
        </details>
      )}
    </div>
  )
}

interface SyncSummaryProps {
  outcome: SyncOutcome
}

function SyncSummary({ outcome }: SyncSummaryProps) {
  const { result, progress_log } = outcome
  const contributionFailures = result.contribution_results.filter(item => item.status === 'failed')
  const feedbackFailures = result.feedback_results.filter(item => item.status === 'failed')
  const totalDownloaded = result.new_contribution_count + result.new_feedback_count
  const totalFailed = contributionFailures.length + feedbackFailures.length
  const totalSucceeded = totalDownloaded - totalFailed

  return (
    <div className="mt-6 rounded-lg border border-mimir-border bg-mimir-surface-soft/60 p-4">
      <div className="text-[12px] font-medium text-mimir-text">Last sync</div>

      {totalDownloaded === 0 ? (
        <p className="mt-1.5 text-[11px] text-mimir-text-subtle">Nothing new -- you're caught up.</p>
      ) : (
        <div className="mt-2.5 flex flex-wrap gap-2">
          <span className="rounded-full bg-white/[0.05] px-2.5 py-1 text-[11px] text-mimir-text-muted">
            {totalDownloaded} downloaded
          </span>
          {totalSucceeded > 0 && (
            <span className="rounded-full bg-mimir-accent-soft px-2.5 py-1 text-[11px] text-mimir-green">
              {totalSucceeded} taken in
            </span>
          )}
          {totalFailed > 0 && (
            <span className="rounded-full bg-mimir-status-red/[0.1] px-2.5 py-1 text-[11px] text-mimir-red">
              {totalFailed} failed
            </span>
          )}
        </div>
      )}

      {totalFailed > 0 && (
        <div className="mt-3 space-y-1.5">
          {contributionFailures.map(item => (
            <FailedItemRow key={`c-${item.file}`} kind="contribution" item={item} />
          ))}
          {feedbackFailures.map(item => (
            <FailedItemRow key={`f-${item.file}`} kind="feedback" item={item} />
          ))}
        </div>
      )}

      {progress_log.trim() && (
        <details className="mt-3 rounded-md border border-mimir-border bg-black/20 p-2.5">
          <summary className="cursor-pointer text-[11px] font-medium text-mimir-text-muted">Raw sync log</summary>
          <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-mimir-text-subtle">
            {progress_log}
          </pre>
        </details>
      )}
    </div>
  )
}

export function DashboardScreen({ ready, onReviewFeedback }: DashboardScreenProps) {
  const [progress, setProgress] = useState<GateProgress | null>(null)
  const [datasetRoot, setDatasetRoot] = useState('')
  const [loadError, setLoadError] = useState<DescribedError | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [syncError, setSyncError] = useState<DescribedError | null>(null)
  const [lastSync, setLastSync] = useState<SyncOutcome | null>(null)
  const [feedbackItems, setFeedbackItems] = useState<FeedbackListItem[]>([])
  const [feedbackReviews, setFeedbackReviews] = useState<Record<string, FeedbackReview>>({})
  const [recentReports, setRecentReports] = useState<ReportSummary[]>([])

  const loadStatus = async () => {
    if (!ready) return
    try {
      const [result, settings, feedback, reviews, reports] = await Promise.all([
        api.getStatus(),
        api.getSettings(),
        api.listFeedback(),
        api.getFeedbackReviews(),
        api.listRecentReports(),
      ])
      setProgress(result)
      setDatasetRoot(settings.dataset_root)
      setFeedbackItems(feedback.items)
      setFeedbackReviews(reviews)
      setRecentReports(reports)
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
      const [feedback, reviews] = await Promise.all([api.listFeedback(), api.getFeedbackReviews()])
      setFeedbackItems(feedback.items)
      setFeedbackReviews(reviews)
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
          className="rounded-md border border-mimir-accent/40 bg-mimir-accent-soft px-4 py-2 text-[12px] font-medium text-mimir-accent disabled:opacity-60"
        >
          <span className="inline-flex items-center gap-2">
            {syncing && <Spinner />}
            {syncing ? 'Syncing...' : 'Sync now'}
          </span>
        </button>
      </div>

      <ErrorNotice error={loadError} className="mt-4" />
      <ErrorNotice error={syncError} className="mt-4" />

      <NeedsReview items={feedbackItems} reviews={feedbackReviews} onReviewFeedback={onReviewFeedback} />

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

      {progress?.pilot_gate_met && datasetRoot && <TrainingCommands datasetRoot={datasetRoot} />}

      {lastSync && <SyncSummary outcome={lastSync} />}

      <RecentReports reports={recentReports} />
    </div>
  )
}
