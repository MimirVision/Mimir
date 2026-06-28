import type { ScanStep } from '../types'

interface ScanProgressProps {
  steps: ScanStep[]
  activeStep: number
  progress: number
  folderName: string
}

export function ScanProgress({ steps, activeStep, progress, folderName }: ScanProgressProps) {
  const currentStep = steps[activeStep] ?? steps[0]

  return (
    <div className="flex h-full min-h-[620px] items-center justify-center px-8 py-8">
      <section className="w-full max-w-[820px] rounded-xl border border-[var(--mimir-border)] bg-[var(--mimir-surface)] p-8 shadow-[0_24px_80px_rgba(0,0,0,0.38)]">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-lg border border-[var(--mimir-border)] bg-black/25">
            <div className="h-2.5 w-2.5 rounded-full bg-[var(--mimir-status-green)]" />
          </div>
          <div className="text-[12px] font-medium uppercase tracking-[0.14em] text-[var(--mimir-text-subtle)]">
            Local scan wizard
          </div>
          <h1 className="mt-3 text-[34px] font-semibold leading-tight text-[var(--mimir-text)]">
            Preparing footage for review
          </h1>
          <p className="mx-auto mt-3 max-w-[520px] text-[15px] leading-7 text-[var(--mimir-text-muted)]">
            Mimir checks your selected folder locally and organizes the review flow before showing results.
          </p>
        </div>

        <div className="mb-7 rounded-lg border border-[var(--mimir-border)] bg-black/20 p-4">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-[12px] text-[var(--mimir-text-subtle)]">Selected footage</div>
              <div className="mt-1 max-w-[520px] truncate text-[14px] font-medium text-[var(--mimir-text)]">
                {folderName || 'TeslaCam folder'}
              </div>
            </div>
            <div className="rounded-full border border-[var(--mimir-border)] bg-white/[0.035] px-3 py-1.5 text-[12px] text-[var(--mimir-text-muted)]">
              Demo state
            </div>
          </div>

          <div className="mb-3 flex items-center justify-between gap-4 text-[13px]">
            <span className="font-medium text-[var(--mimir-text)]">{currentStep.label}</span>
            <span className="text-right text-[var(--mimir-text-muted)]">{currentStep.description}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.08]" aria-label="Demo scan progress">
            <div
              className="h-full rounded-full bg-[var(--mimir-text)] transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
          <div className="mt-2 text-[12px] text-[var(--mimir-text-subtle)]">
            Progress is simulated for this frontend preview.
          </div>
        </div>

        <ol className="grid gap-3 md:grid-cols-4">
          {steps.map((step, index) => {
            const isDone = index < activeStep
            const isActive = index === activeStep
            return (
              <li
                key={step.id}
                className={`rounded-lg border p-4 transition ${
                  isActive
                    ? 'border-[var(--mimir-border-strong)] bg-white/[0.055]'
                    : isDone
                      ? 'border-[var(--mimir-border)] bg-white/[0.035]'
                      : 'border-white/[0.06] bg-black/18'
                }`}
              >
                <div className="mb-4 flex items-center gap-3">
                  <div
                    className={`grid h-7 w-7 place-items-center rounded-full border text-[12px] font-semibold ${
                      isActive
                        ? 'border-white/60 bg-[var(--mimir-text)] text-black'
                        : isDone
                          ? 'border-[var(--mimir-status-green)] text-[var(--mimir-status-green)]'
                          : 'border-white/12 text-[var(--mimir-text-subtle)]'
                    }`}
                  >
                    {index + 1}
                  </div>
                  <div className="text-[13px] font-medium text-[var(--mimir-text)]">{step.label}</div>
                </div>
                <div className="text-[12px] leading-5 text-[var(--mimir-text-muted)]">{step.description}</div>
              </li>
            )
          })}
        </ol>

        <div className="mt-7 flex flex-wrap items-center justify-between gap-3">
          <details className="rounded-lg border border-[var(--mimir-border)] bg-white/[0.025] px-4 py-3">
            <summary className="cursor-pointer text-[13px] font-medium text-[var(--mimir-text-muted)] transition hover:text-[var(--mimir-text)]">
              Scan details
            </summary>
            <div className="mt-3 max-w-[420px] text-[12px] leading-6 text-[var(--mimir-text-subtle)]">
              Future diagnostics can appear here without exposing raw terminal output by default.
            </div>
          </details>
          <button
            disabled
            aria-busy="true"
            className="h-11 cursor-wait rounded-lg bg-[var(--mimir-text)] px-6 text-[14px] font-semibold text-black shadow-[0_10px_30px_rgba(255,255,255,0.08)]"
          >
            Analyzing locally
          </button>
        </div>
      </section>
    </div>
  )
}
