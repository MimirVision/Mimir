import { useEffect, useState } from 'react'
import { convertFileSrc } from '@tauri-apps/api/core'
import { api } from '../lib/api'
import { describeError, type DescribedError } from '../lib/errorMessages'
import { ErrorNotice } from '../components/ErrorNotice'
import type { FeedbackDetail, FeedbackListItem } from '../lib/types'

interface FeedbackScreenProps {
  ready: boolean
}

export function FeedbackScreen({ ready }: FeedbackScreenProps) {
  const [items, setItems] = useState<FeedbackListItem[]>([])
  const [listError, setListError] = useState<DescribedError | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<FeedbackDetail | null>(null)
  const [detailError, setDetailError] = useState<DescribedError | null>(null)

  const loadList = async () => {
    if (!ready) return
    try {
      const result = await api.listFeedback()
      setItems(result.items)
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
        }
      })
      .catch(err => {
        if (!cancelled) setDetailError(describeError(err, 'Could not load that feedback item.'))
      })
    return () => {
      cancelled = true
    }
  }, [selectedId])

  if (!ready) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-8">
        <p className="text-[13px] text-mimir-text-muted">Set your feedback inbox path in Settings first.</p>
      </div>
    )
  }

  return (
    <div className="mx-auto flex max-w-4xl gap-6 px-6 py-8">
      <div className="w-72 shrink-0">
        <h1 className="text-lg font-medium text-mimir-text">Feedback</h1>
        <ErrorNotice error={listError} className="mt-3" />
        {items.length === 0 && !listError && (
          <p className="mt-3 text-[12px] text-mimir-text-subtle">No feedback received yet.</p>
        )}
        <div className="mt-3 space-y-1.5">
          {items.map(item => {
            const choice = item.feedback.user_selected_feedback ?? '?'
            const timestamp = item.feedback.timestamp ?? item.feedback.saved_at ?? ''
            const active = item.package_id === selectedId
            return (
              <button
                key={item.package_id}
                type="button"
                onClick={() => setSelectedId(item.package_id)}
                className={`block w-full rounded-md border px-3 py-2 text-left text-[12px] ${
                  active
                    ? 'border-mimir-accent/40 bg-mimir-accent-soft text-mimir-text'
                    : 'border-mimir-border bg-mimir-surface-soft/60 text-mimir-text-muted'
                }`}
              >
                <div className="font-medium">{choice}</div>
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
          <div className="rounded-lg border border-mimir-border bg-mimir-surface-soft/60 p-4">
            {detail.video_path && (
              <video
                src={convertFileSrc(detail.video_path)}
                controls
                className="mb-4 w-full rounded-md border border-mimir-border bg-black"
              />
            )}
            <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-mimir-text-muted">
              {JSON.stringify(detail.feedback, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  )
}
