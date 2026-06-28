import mimirLockup from '../assets/mimir-lockup.png'

interface ImportPanelProps {
  selectedFolder: string
  onChooseFolder: () => void
  onLoadLatestSession: () => void
  onPreviewSample: () => void
  loadState: 'idle' | 'loading' | 'loaded' | 'missing' | 'error'
}

export function ImportPanel({
  selectedFolder,
  onChooseFolder,
  onLoadLatestSession,
  onPreviewSample,
  loadState,
}: ImportPanelProps) {
  return (
    <div className="flex h-full min-h-[620px] items-center justify-center px-8 py-8">
      <section className="w-full max-w-[760px]">
        <div className="mb-10 text-center">
          <img src={mimirLockup} alt="Mimir" className="mx-auto mb-10 h-20 w-auto opacity-95" />
          <h1 className="text-[46px] font-semibold leading-[1.04] text-[var(--mimir-text)]">
            Review Sentry footage in minutes
          </h1>
          <p className="mx-auto mt-4 max-w-[460px] text-[17px] leading-7 text-[var(--mimir-text-muted)]">
            Private local analysis. No upload required.
          </p>
        </div>

        <div className="rounded-xl border border-[var(--mimir-border)] bg-[var(--mimir-surface)] p-3 shadow-[0_24px_80px_rgba(0,0,0,0.38)]">
          <button
            onClick={onChooseFolder}
            className="group flex min-h-[210px] w-full items-center justify-center rounded-lg border border-dashed border-[var(--mimir-border-strong)] bg-black/20 px-8 text-center transition hover:border-white/28 hover:bg-white/[0.035]"
          >
            <div className="max-w-[520px]">
              <div className="mx-auto mb-5 grid h-14 w-14 place-items-center rounded-lg border border-[var(--mimir-border)] bg-[var(--mimir-surface-soft)] text-[24px] font-light text-[var(--mimir-text)] transition group-hover:bg-[var(--mimir-surface-muted)]">
                +
              </div>
              <div className="text-[18px] font-semibold text-[var(--mimir-text)]">
                Choose TeslaCam Folder
              </div>
              <div className="mt-2 text-[14px] leading-6 text-[var(--mimir-text-muted)]">
                Select the TeslaCam or dashcam folder from your USB drive.
              </div>
              {selectedFolder && (
                <div className="mx-auto mt-5 max-w-[420px] truncate rounded-full border border-[var(--mimir-border)] bg-white/[0.035] px-4 py-2 text-[13px] text-[var(--mimir-text)]">
                  {selectedFolder}
                </div>
              )}
            </div>
          </button>

          <div className="mt-3 flex flex-wrap items-center justify-between gap-3 px-1">
            <div className="text-[13px] text-[var(--mimir-text-subtle)]">
              Footage stays on this computer.
            </div>
            <div className="flex flex-wrap gap-3">
              <button
                onClick={onPreviewSample}
                className="h-11 rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] px-4 text-[13px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.05] hover:text-[var(--mimir-text)]"
              >
                Preview UI with sample data
              </button>
              <button
                onClick={onLoadLatestSession}
                disabled={loadState === 'loading'}
                className="h-11 rounded-lg bg-[var(--mimir-text)] px-6 text-[14px] font-semibold text-black shadow-[0_10px_30px_rgba(255,255,255,0.08)] transition hover:bg-white disabled:cursor-wait disabled:opacity-70"
              >
                {loadState === 'loading' ? 'Loading...' : 'Load Latest Session'}
              </button>
            </div>
          </div>

          {loadState === 'missing' && (
            <div className="mt-3 rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] p-4 text-[13px] text-[var(--mimir-text-muted)]">
              No scan results found yet.
            </div>
          )}

          {loadState === 'error' && (
            <div className="mt-3 rounded-lg border border-red-400/20 bg-red-500/10 p-4 text-[13px] text-red-100">
              Could not read Mimir session output.
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
