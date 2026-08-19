import { useCallback, useEffect, useState } from 'react'
import { convertFileSrc } from '@tauri-apps/api/core'
import { api } from '../lib/api'
import { describeError, type DescribedError } from '../lib/errorMessages'
import { ErrorNotice } from '../components/ErrorNotice'
import { Spinner } from '../components/Spinner'
import type { LabelCandidate } from '../lib/types'

/**
 * Give a verdict to one event group at a time.
 *
 * The evaluation set the model card requires is 750 groups, and it did not
 * exist because the only way to build it was editing a CSV against a folder of
 * contact sheets. This is that loop with the friction removed: the sheet on the
 * left, three keys on the right, next group.
 *
 * Deliberately absent: any button that accepts Mimir's own verdict in one
 * click. The set exists to measure Mimir, and a one-key "agree" makes agreement
 * the cheapest action on every borderline group -- which is exactly where the
 * measurement has to be honest. Mimir's call is shown as context, greyed, and
 * costs the same three keystrokes as disagreeing with it.
 */

const SEVERITIES = [
  { value: 'IMPORTANT', key: '1', hint: 'something touched the car' },
  { value: 'REVIEW', key: '2', hint: 'cannot tell, worth a look' },
  { value: 'IGNORE', key: '3', hint: 'nothing happened' },
] as const

export function LabelScreen({ sourceSet }: { sourceSet: string }) {
  const [queue, setQueue] = useState<LabelCandidate[]>([])
  const [categories, setCategories] = useState<string[]>([])
  const [alreadyDone, setAlreadyDone] = useState(0)
  const [severity, setSeverity] = useState<string>('')
  const [category, setCategory] = useState<string>('')
  const [notes, setNotes] = useState('')
  const [saved, setSaved] = useState(0)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<DescribedError | null>(null)

  const current = queue[0]

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    api
      .listLabelCandidates(200)
      .then(result => {
        setQueue(result.pending)
        setCategories(result.categories)
        setAlreadyDone(result.skipped_already_labelled)
      })
      .catch(problem => setError(describeError(problem, 'Could not load groups to label.')))
      .finally(() => setLoading(false))
  }, [])

  useEffect(load, [load])

  const commit = () => {
    if (!current || !severity || !category || busy) {
      return
    }

    setBusy(true)
    setError(null)
    api
      .saveLabel(current.filename_or_group, severity, category, notes, sourceSet)
      .then(result => {
        if (!result.saved) {
          setError(describeError(null, `Not saved: ${result.reason ?? 'unknown reason'}`))
          return
        }
        setSaved(count => count + 1)
        setQueue(rest => rest.slice(1))
        setSeverity('')
        setCategory('')
        setNotes('')
      })
      .catch(problem => setError(describeError(problem, 'Could not save that verdict.')))
      .finally(() => setBusy(false))
  }

  // 1/2/3 pick a verdict; the category still has to be chosen, so nothing is
  // ever recorded by a single keystroke.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) {
        return
      }
      const match = SEVERITIES.find(option => option.key === event.key)
      if (match) {
        event.preventDefault()
        setSeverity(match.value)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (loading) {
    return (
      <p className="flex items-center gap-2 text-sm text-slate-400">
        <Spinner /> Loading groups that still need a verdict…
      </p>
    )
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Labelling</h1>
          <p className="text-sm text-slate-400">
            {queue.length} waiting · {saved} done this session · {alreadyDone} already labelled
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
        >
          Reload
        </button>
      </header>

      <ErrorNotice error={error} />

      {!current && (
        <p className="rounded-xl border border-slate-800 bg-slate-900/50 p-6 text-sm text-slate-400">
          Nothing left in this scan. Point Forge at another scan session in Settings to keep going.
        </p>
      )}

      {current && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
          <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-3">
            {current.contact_sheet ? (
              <img
                src={convertFileSrc(current.contact_sheet)}
                alt={`Contact sheet for ${current.source}`}
                className="w-full rounded-lg"
              />
            ) : (
              <p className="p-6 text-sm text-slate-400">
                No contact sheet for this group. Judge it from the evidence on the right, or skip it.
              </p>
            )}
            <p className="mt-2 truncate text-xs text-slate-500" title={current.source}>
              {current.source}
            </p>
          </section>

          <section className="space-y-4">
            <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4 text-sm">
              <h2 className="mb-2 font-medium text-slate-200">Did anything touch the car?</h2>
              <div className="space-y-1.5">
                {SEVERITIES.map(option => (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setSeverity(option.value)}
                    className={`flex w-full items-center justify-between rounded-lg border px-3 py-2 text-left transition ${
                      severity === option.value
                        ? 'border-sky-500 bg-sky-500/10 text-slate-100'
                        : 'border-slate-700 text-slate-300 hover:bg-slate-800'
                    }`}
                  >
                    <span>
                      {option.value}
                      <span className="ml-2 text-xs text-slate-500">{option.hint}</span>
                    </span>
                    <kbd className="rounded bg-slate-800 px-1.5 text-xs text-slate-400">{option.key}</kbd>
                  </button>
                ))}
              </div>

              <label className="mt-3 block text-xs text-slate-400">
                Category
                <select
                  value={category}
                  onChange={event => setCategory(event.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-200"
                >
                  <option value="">Choose one…</option>
                  {categories.map(name => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="mt-3 block text-xs text-slate-400">
                Notes (optional)
                <input
                  value={notes}
                  onChange={event => setNotes(event.target.value)}
                  placeholder="why, if it is not obvious"
                  className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-200"
                />
              </label>

              <button
                type="button"
                onClick={commit}
                disabled={!severity || !category || busy}
                className="mt-3 w-full rounded-lg bg-sky-600 px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-500"
              >
                {busy ? 'Saving…' : 'Save and next'}
              </button>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-900/30 p-4 text-xs text-slate-500">
              <h3 className="mb-2 font-medium text-slate-400">What Mimir said — context, not the answer</h3>
              <dl className="space-y-1">
                <div className="flex justify-between gap-3">
                  <dt>Verdict</dt>
                  <dd className="text-slate-300">{current.mimir_said || '—'}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt>Impact / contact</dt>
                  <dd className="text-slate-300">
                    {current.impact_level || '—'} / {current.contact_level || '—'}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt>Detected</dt>
                  <dd className="text-slate-300">{current.detected || '—'}</dd>
                </div>
              </dl>
              {current.mimir_reasons && <p className="mt-2 leading-5">{current.mimir_reasons}</p>}
            </div>
          </section>
        </div>
      )}
    </div>
  )
}
