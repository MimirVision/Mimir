import { useEffect, useMemo, useState } from 'react'
import { convertFileSrc } from '@tauri-apps/api/core'
import { api } from '../lib/api'
import { describeError, type DescribedError } from '../lib/errorMessages'
import { ErrorNotice } from '../components/ErrorNotice'
import type { FeedbackDetail, FeedbackListItem, FeedbackReview } from '../lib/types'

interface FeedbackScreenProps {
  ready: boolean
}

// Reviewing feedback here does not by itself change the model -- unlike a
// Contribution, feedback never went through clip-level rights confirmation
// (see C:\Mimir\docs\DATA_CONTRIBUTION.md), so it can't be folded into the
// training dataset automatically. Mimir itself now nudges testers to also
// hit Contribute when they pick a choice that flags a real detector error
// (see AiFeedbackPanel in IncidentViewerScreen.tsx), so the developer's job
// on those items is mainly to check whether a matching contribution showed
// up in Collections -- and to reach out directly if it didn't, since that
// nudge only exists for feedback submitted after it shipped. Review tracking
// below just makes sure that judgment call doesn't evaporate between
// sessions.
const PROMOTABLE_CHOICES = new Set(['Weird AI flag', 'Missed obvious event'])

function promotionNote(choice: string): string {
  if (PROMOTABLE_CHOICES.has(choice)) {
    return 'This choice flags a real detector error -- Mimir already nudged the tester to also Contribute the incident. Check Collections for a matching contribution; if none turns up, reach out and ask them to submit one.'
  }
  return 'Feedback is developer signal, not training data -- it skipped the rights confirmation a Contribution requires, so it stays out of the dataset regardless of what you decide here.'
}

export function FeedbackScreen({ ready }: FeedbackScreenProps) {
  const [items, setItems] = useState<FeedbackListItem[]>([])
  const [reviews, setReviews] = useState<Record<string, FeedbackReview>>({})
  const [listError, setListError] = useState<DescribedError | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<FeedbackDetail | null>(null)
  const [detailError, setDetailError] = useState<DescribedError | null>(null)
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)

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

  const setReviewed = async (reviewed: boolean) => {
    if (!selectedId) return
    setSaving(true)
    try {
      await api.saveFeedbackReview(selectedId, reviewed, note)
      const updated = await api.getFeedbackReviews()
      setReviews(updated)
    } catch (err) {
      setDetailError(describeError(err, 'Could not save that review.'))
    } finally {
      setSaving(false)
    }
  }

  const saveNote = async () => {
    if (!selectedId) return
    setSaving(true)
    try {
      const alreadyReviewed = reviews[selectedId]?.reviewed ?? false
      await api.saveFeedbackReview(selectedId, alreadyReviewed, note)
      const updated = await api.getFeedbackReviews()
      setReviews(updated)
    } catch (err) {
      setDetailError(describeError(err, 'Could not save that note.'))
    } finally {
      setSaving(false)
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
        <ErrorNotice error={listError} className="mt-3" />
        {items.length === 0 && !listError && (
          <p className="mt-3 text-[12px] text-mimir-text-subtle">No feedback received yet.</p>
        )}
        <div className="mt-3 space-y-1.5">
          {sortedItems.map(item => {
            const choice = item.feedback.user_selected_feedback ?? '?'
            const timestamp = item.feedback.timestamp ?? item.feedback.saved_at ?? ''
            const active = item.package_id === selectedId
            const reviewed = reviews[item.package_id]?.reviewed ?? false
            return (
              <button
                key={item.package_id}
                type="button"
                onClick={() => setSelectedId(item.package_id)}
                className={`block w-full rounded-md border px-3 py-2 text-left text-[12px] ${
                  active
                    ? 'border-mimir-accent/40 bg-mimir-accent-soft text-mimir-text'
                    : 'border-mimir-border bg-mimir-surface-soft/60 text-mimir-text-muted'
                } ${reviewed ? 'opacity-60' : ''}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{choice}</span>
                  {reviewed && <span className="text-[9px] text-mimir-green">reviewed</span>}
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
              <div className="flex items-center justify-between">
                <span className="text-[12px] font-medium text-mimir-text">Review</span>
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => setReviewed(!(selectedReview?.reviewed ?? false))}
                  className={`rounded-md border px-3 py-1.5 text-[11px] font-medium disabled:opacity-40 ${
                    selectedReview?.reviewed
                      ? 'border-mimir-border text-mimir-text-muted'
                      : 'border-mimir-accent/40 bg-mimir-accent-soft text-mimir-accent'
                  }`}
                >
                  {selectedReview?.reviewed ? 'Mark unreviewed' : 'Mark reviewed'}
                </button>
              </div>
              <textarea
                value={note}
                onChange={event => setNote(event.target.value)}
                onBlur={saveNote}
                placeholder="What did you do about this? (e.g. filed a bug, adjusted a threshold, asked for a resubmit as a Contribution)"
                rows={3}
                className="mt-2.5 w-full resize-none rounded-md border border-mimir-border bg-mimir-bg-depth px-2.5 py-1.5 text-[12px] text-mimir-text outline-none focus-visible:border-mimir-accent"
              />
              <p className="mt-2 text-[10px] leading-4 text-mimir-text-subtle">
                {promotionNote(String(detail.feedback.user_selected_feedback ?? ''))}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
