import { useEffect, useMemo, useState } from 'react'
import { convertFileSrc } from '@tauri-apps/api/core'
import { api } from '../lib/api'
import { describeError, type DescribedError } from '../lib/errorMessages'
import { ErrorNotice } from '../components/ErrorNotice'
import { ModalOverlay } from '../components/ModalOverlay'
import { Spinner } from '../components/Spinner'
import type { FeedbackCategory, FeedbackDetail, FeedbackListItem, FeedbackReview } from '../lib/types'

interface FeedbackScreenProps {
  ready: boolean
  /** Package id to jump straight to, e.g. from Dashboard's needs-review list. */
  focusId?: string | null
  /** Called once focusId has been applied, so it doesn't keep overriding manual selection. */
  onFocusConsumed?: () => void
}

// Reviewing feedback here does not by itself change the model -- unlike a
// Contribution, feedback never went through clip-level rights confirmation
// (see C:\Mimir\docs\DATA_CONTRIBUTION.md), so it can't be folded into the
// training dataset automatically. Mimir itself now nudges testers to also
// hit Contribute when they pick a choice that flags a real detector error
// (see AiFeedbackPanel in IncidentViewerScreen.tsx), so the developer's job
// on those items is mainly to check whether a matching contribution showed
// up in Collections -- and to reach out directly if it didn't, since that
// nudge only exists for feedback submitted after it shipped.
const PROMOTABLE_CHOICES = new Set(['Weird AI flag', 'Missed obvious event'])

function promotionNote(choice: string): string {
  if (PROMOTABLE_CHOICES.has(choice)) {
    return 'This choice flags a real detector error -- Mimir already nudged the tester to also Contribute the incident. Check Collections for a matching contribution; if none turns up, reach out and ask them to submit one.'
  }
  return 'Feedback is developer signal, not training data -- it skipped the rights confirmation a Contribution requires, so it stays out of the dataset regardless of what you decide here.'
}

// The category is what makes review work into something that goes
// somewhere: it's what Generate report groups by, so picking one is the
// actual hand-off point between "I looked at this" and "here's what to do
// about it."
const CATEGORIES: Array<{ id: FeedbackCategory; label: string; hint: string }> = [
  { id: 'bug', label: 'Bug / logic fix', hint: 'A code or threshold issue -- bring it to Claude.' },
  { id: 'training_gap', label: 'Needs training data', hint: 'A real detection gap -- chase a Contribution.' },
  { id: 'no_action', label: 'No action', hint: 'Noise, duplicate, or already correct.' },
]

export function FeedbackScreen({ ready, focusId, onFocusConsumed }: FeedbackScreenProps) {
  const [items, setItems] = useState<FeedbackListItem[]>([])
  const [reviews, setReviews] = useState<Record<string, FeedbackReview>>({})
  const [listError, setListError] = useState<DescribedError | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<FeedbackDetail | null>(null)
  const [detailError, setDetailError] = useState<DescribedError | null>(null)
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [reportOpen, setReportOpen] = useState(false)
  const [reportMarkdown, setReportMarkdown] = useState('')
  const [reportPath, setReportPath] = useState('')
  const [reportCopied, setReportCopied] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [reportError, setReportError] = useState<DescribedError | null>(null)

  const loadList = async () => {
    if (!ready) return
    try {
      const [listResult, reviewResult] = await Promise.all([api.listFeedback(), api.getFeedbackReviews()])
      setItems(listResult.items)
      setReviews(reviewResult)
      setListError(null)
    } catch (err) {
      setListError(describeError(err, 'Could not load feedback.'))
    }
  }

  useEffect(() => {
    void loadList()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready])

  useEffect(() => {
    if (!focusId || items.length === 0) return
    setSelectedId(focusId)
    onFocusConsumed?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusId, items])

  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      return
    }
    let cancelled = false
    api
      .showFeedback(selectedId)
      .then(result => {
        if (!cancelled) {
          setDetail(result)
          setDetailError(null)
          setNote(reviews[selectedId]?.note ?? '')
        }
      })
      .catch(err => {
        if (!cancelled) setDetailError(describeError(err, 'Could not load that feedback item.'))
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId])

  // Unreviewed items first so nothing new gets lost under a pile of already-handled ones.
  const sortedItems = useMemo(
    () =>
      [...items].sort((a, b) => {
        const aReviewed = reviews[a.package_id]?.reviewed ?? false
        const bReviewed = reviews[b.package_id]?.reviewed ?? false
        if (aReviewed === bReviewed) return 0
        return aReviewed ? 1 : -1
      }),
    [items, reviews],
  )

  const persistReview = async (reviewed: boolean, category: FeedbackCategory) => {
    if (!selectedId) return
    setSaving(true)
    try {
      await api.saveFeedbackReview(selectedId, reviewed, note, category)
      const updated = await api.getFeedbackReviews()
      setReviews(updated)
    } catch (err) {
      setDetailError(describeError(err, 'Could not save that review.'))
    } finally {
      setSaving(false)
    }
  }

  const pickCategory = (category: FeedbackCategory) => {
    const current = selectedId ? reviews[selectedId] : undefined
    const next = current?.category === category ? '' : category
    void persistReview(Boolean(next), next)
  }

  const saveNote = async () => {
    if (!selectedId) return
    const current = reviews[selectedId]
    await persistReview(current?.reviewed ?? false, current?.category ?? '')
  }

  const generateReport = async () => {
    setGenerating(true)
    setReportError(null)
    try {
      const report = await api.generateFeedbackReport()
      setReportMarkdown(report.markdown)
      setReportPath(report.path)
      setReportOpen(true)
    } catch (err) {
      setReportError(describeError(err, 'Could not generate the report.'))
    } finally {
      setGenerating(false)
    }
  }

  const copyReport = async () => {
    try {
      await navigator.clipboard.writeText(reportMarkdown)
      setReportCopied(true)
      setTimeout(() => setReportCopied(false), 1500)
    } catch {
      // Clipboard access can be denied by the OS; the report is still on
      // disk and visible in the modal to copy by hand.
    }
  }

  if (!ready) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-8">
        <p className="text-[13px] text-mimir-text-muted">Set your feedback inbox path in Settings first.</p>
      </div>
    )
  }

  const unreviewedCount = items.filter(item => !reviews[item.package_id]?.reviewed).length
  const selectedReview = selectedId ? reviews[selectedId] : undefined

  return (
    <div className="mx-auto flex max-w-4xl gap-6 px-6 py-8">
      <div className="w-72 shrink-0">
        <div className="flex items-baseline justify-between">
          <h1 className="text-lg font-medium text-mimir-text">Feedback</h1>
          {unreviewedCount > 0 && (
            <span className="rounded-full bg-mimir-accent-soft px-2 py-0.5 text-[10px] font-medium text-mimir-accent">
              {unreviewedCount} new
            </span>
          )}
        </div>

        <button
          type="button"
          disabled={generating || items.length === 0}
          onClick={generateReport}
          className="mt-3 flex w-full items-center justify-center gap-2 rounded-md border border-mimir-accent/40 bg-mimir-accent-soft px-3 py-2 text-[12px] font-medium text-mimir-accent disabled:opacity-40"
        >
          {generating && <Spinner />}
          Generate report
        </button>
        <ErrorNotice error={reportError} className="mt-2" />

        <ErrorNotice error={listError} className="mt-3" />
        {items.length === 0 && !listError && (
          <p className="mt-3 text-[12px] text-mimir-text-subtle">No feedback received yet.</p>
        )}
        <div className="mt-3 space-y-1.5">
          {sortedItems.map(item => {
            const choice = item.feedback.user_selected_feedback ?? '?'
            const timestamp = item.feedback.timestamp ?? item.feedback.saved_at ?? ''
            const active = item.package_id === selectedId
            const review = reviews[item.package_id]
            const categoryLabel = CATEGORIES.find(c => c.id === review?.category)?.label
            return (
              <button
                key={item.package_id}
                type="button"
                onClick={() => setSelectedId(item.package_id)}
                className={`block w-full rounded-md border px-3 py-2 text-left text-[12px] ${
                  active
                    ? 'border-mimir-accent/40 bg-mimir-accent-soft text-mimir-text'
                    : 'border-mimir-border bg-mimir-surface-soft/60 text-mimir-text-muted'
                } ${review?.reviewed ? 'opacity-60' : ''}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{choice}</span>
                  {review?.reported_at ? (
                    <span className="text-[9px] text-mimir-text-subtle">sent</span>
                  ) : (
                    categoryLabel && <span className="text-[9px] text-mimir-green">{categoryLabel}</span>
                  )}
                </div>
                <div className="mt-0.5 text-[10px] text-mimir-text-subtle">
                  {item.package_id.slice(0, 12)}... {String(timestamp)}
                </div>
              </button>
            )
          })}
        </div>
      </div>

      <div className="min-w-0 flex-1">
        <ErrorNotice error={detailError} />
        {!selectedId && <p className="text-[12px] text-mimir-text-subtle">Select a feedback item.</p>}
        {detail && (
          <div className="space-y-4">
            <div className="rounded-lg border border-mimir-border bg-mimir-surface-soft/60 p-4">
              {detail.video_path && (
                <video
                  src={convertFileSrc(detail.video_path)}
                  controls
                  className="mb-4 w-full rounded-md border border-mimir-border bg-black"
                />
              )}
              <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-mimir-text-muted">
                {JSON.stringify(detail.feedback, null, 2)}
              </pre>
            </div>

            <div className="rounded-lg border border-mimir-border bg-mimir-surface-soft/60 p-4">
              <span className="text-[12px] font-medium text-mimir-text">What is this?</span>
              <div className="mt-2.5 grid grid-cols-3 gap-2">
                {CATEGORIES.map(category => (
                  <button
                    key={category.id}
                    type="button"
                    disabled={saving}
                    onClick={() => pickCategory(category.id)}
                    title={category.hint}
                    className={`rounded-md border px-2 py-2 text-[11px] font-medium disabled:opacity-40 ${
                      selectedReview?.category === category.id
                        ? 'border-mimir-accent/40 bg-mimir-accent-soft text-mimir-accent'
                        : 'border-mimir-border text-mimir-text-muted'
                    }`}
                  >
                    {category.label}
                  </button>
                ))}
              </div>
              <textarea
                value={note}
                onChange={event => setNote(event.target.value)}
                onBlur={saveNote}
                placeholder="What did you do about this? (e.g. filed a bug, adjusted a threshold, asked for a resubmit as a Contribution)"
                rows={3}
                className="mt-3 w-full resize-none rounded-md border border-mimir-border bg-mimir-bg-depth px-2.5 py-1.5 text-[12px] text-mimir-text outline-none focus-visible:border-mimir-accent"
              />
              <p className="mt-2 text-[10px] leading-4 text-mimir-text-subtle">
                {promotionNote(String(detail.feedback.user_selected_feedback ?? ''))}
              </p>
            </div>
          </div>
        )}
      </div>

      {reportOpen && (
        <ModalOverlay label="Feedback report" onClose={() => setReportOpen(false)} closeOnBackdrop>
          <div className="max-h-[80vh] w-[min(90vw,720px)] overflow-hidden rounded-xl border border-mimir-border bg-mimir-surface p-5">
            <div className="flex items-center justify-between">
              <h2 className="text-[14px] font-medium text-mimir-text">Feedback report</h2>
              <button
                type="button"
                onClick={() => setReportOpen(false)}
                className="rounded-md border border-mimir-border px-2.5 py-1 text-[11px] text-mimir-text-muted"
              >
                Close
              </button>
            </div>
            <p className="mt-1.5 text-[11px] text-mimir-text-subtle">
              Saved to {reportPath}. Copy this and paste it into a chat to hand it off.
            </p>
            <pre className="mt-3 max-h-[55vh] overflow-auto whitespace-pre-wrap rounded-md border border-mimir-border bg-black/25 p-3 text-[11px] leading-5 text-mimir-text-muted">
              {reportMarkdown}
            </pre>
            <button
              type="button"
              onClick={copyReport}
              className="mt-3 rounded-md border border-mimir-accent/40 bg-mimir-accent-soft px-3 py-1.5 text-[12px] font-medium text-mimir-accent"
            >
              {reportCopied ? 'Copied' : 'Copy to clipboard'}
            </button>
          </div>
        </ModalOverlay>
      )}
    </div>
  )
}
