import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { describeError, type DescribedError } from '../lib/errorMessages'
import { ErrorNotice } from '../components/ErrorNotice'
import type { CollectionDetail, CollectionListItem } from '../lib/types'

interface CollectionsScreenProps {
  ready: boolean
}

function statusBadgeClass(status: string): string {
  if (status === 'complete') return 'bg-mimir-accent-soft text-mimir-green'
  if (status === 'pending') return 'bg-mimir-status-amber/15 text-mimir-amber'
  if (status === 'not_requested') return 'bg-white/5 text-mimir-text-subtle'
  return 'bg-mimir-status-red/15 text-mimir-red'
}

export function CollectionsScreen({ ready }: CollectionsScreenProps) {
  const [items, setItems] = useState<CollectionListItem[]>([])
  const [listError, setListError] = useState<DescribedError | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<CollectionDetail | null>(null)
  const [detailError, setDetailError] = useState<DescribedError | null>(null)
  const [opening, setOpening] = useState<number | null>(null)

  const loadList = async () => {
    if (!ready) return
    try {
      const result = await api.listCollections()
      setItems(result.items)
      setListError(null)
    } catch (err) {
      setListError(describeError(err, 'Could not load collections.'))
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
      .showCollection(selectedId)
      .then(result => {
        if (!cancelled) {
          setDetail(result)
          setDetailError(null)
        }
      })
      .catch(err => {
        if (!cancelled) setDetailError(describeError(err, 'Could not load that collection.'))
      })
    return () => {
      cancelled = true
    }
  }, [selectedId])

  const openTask = async (taskId: number) => {
    setOpening(taskId)
    try {
      await api.openInCvat(taskId)
    } catch (err) {
      setDetailError(describeError(err, 'Could not open CVAT.'))
    } finally {
      setOpening(null)
    }
  }

  if (!ready) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-8">
        <p className="text-[13px] text-mimir-text-muted">Set your dataset root in Settings first.</p>
      </div>
    )
  }

  return (
    <div className="mx-auto flex max-w-4xl gap-6 px-6 py-8">
      <div className="w-72 shrink-0">
        <h1 className="text-lg font-medium text-mimir-text">Collections</h1>
        <ErrorNotice error={listError} className="mt-3" />
        {items.length === 0 && !listError && (
          <p className="mt-3 text-[12px] text-mimir-text-subtle">No contributions received yet.</p>
        )}
        <div className="mt-3 space-y-1.5">
          {items.map(item => {
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
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{item.package_id.slice(0, 12)}...</span>
                  <span className={`rounded-full px-1.5 py-0.5 text-[9px] font-medium ${statusBadgeClass(item.cvat_status)}`}>
                    {item.cvat_status}
                  </span>
                </div>
                <div className="mt-0.5 text-[10px] text-mimir-text-subtle">
                  {item.split || 'unsplit'} -- {item.imported_at}
                </div>
              </button>
            )
          })}
        </div>
      </div>

      <div className="min-w-0 flex-1">
        <ErrorNotice error={detailError} />
        {!selectedId && <p className="text-[12px] text-mimir-text-subtle">Select a collection.</p>}
        {detail && (
          <div className="space-y-4">
            <div className="rounded-lg border border-mimir-border bg-mimir-surface-soft/60 p-4">
              <div className="text-[12px] font-medium text-mimir-text">Consent</div>
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px]">
                <dt className="text-mimir-text-subtle">Recorded by</dt>
                <dd className="text-mimir-text-muted">{detail.consent.recorded_by || '--'}</dd>
                <dt className="text-mimir-text-subtle">Rights basis</dt>
                <dd className="text-mimir-text-muted">{detail.consent.rights_basis || '--'}</dd>
                <dt className="text-mimir-text-subtle">Reference</dt>
                <dd className="text-mimir-text-muted">{detail.consent.permission_reference || '--'}</dd>
                <dt className="text-mimir-text-subtle">Items</dt>
                <dd className="text-mimir-text-muted">{detail.item_count}</dd>
              </dl>
            </div>

            {detail.live_cvat_tasks.length > 0 && (
              <div className="rounded-lg border border-mimir-border bg-mimir-surface-soft/60 p-4">
                <div className="text-[12px] font-medium text-mimir-text">CVAT tasks</div>
                <div className="mt-2 space-y-1.5">
                  {detail.live_cvat_tasks.map((task, index) => (
                    <div
                      key={task.task_id ?? index}
                      className="flex items-center justify-between rounded-md border border-mimir-border bg-mimir-bg-depth px-2.5 py-1.5 text-[11px]"
                    >
                      <span className="text-mimir-text-muted">
                        {task.name || `task ${task.task_id ?? '?'}`} -- {task.error ?? task.status ?? 'unknown'}
                      </span>
                      {typeof task.task_id === 'number' && (
                        <button
                          type="button"
                          disabled={opening === task.task_id}
                          onClick={() => openTask(task.task_id!)}
                          className="shrink-0 rounded-md border border-mimir-border-strong px-2 py-1 text-[10px] text-mimir-text"
                        >
                          Open in CVAT
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="rounded-lg border border-mimir-border bg-mimir-surface-soft/60 p-4">
              <div className="text-[12px] font-medium text-mimir-text">Raw record</div>
              <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-mimir-text-muted">
                {JSON.stringify(detail.record, null, 2)}
              </pre>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
