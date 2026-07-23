import { useState } from 'react'
import mimirMark from '../assets/mimir-mark.png'
import type { ScanMode } from '../types'

interface OnboardingFlowProps {
  initialScanMode: ScanMode
  onComplete: (scanMode: ScanMode, chooseFolder: boolean) => void
}

const steps = ['Welcome', 'Privacy', 'Scan mode', 'Ready']

const scanModeOptions: Array<{
  value: ScanMode
  title: string
  description: string
}> = [
  {
    value: 'fast',
    title: 'Fast',
    description: 'Quick review for large folders.',
  },
  {
    value: 'balanced',
    title: 'Balanced',
    description: 'Recommended for most scans.',
  },
  {
    value: 'quality',
    title: 'Thorough',
    description: 'Slower, more careful review.',
  },
]

const privacyNotes = [
  'Processing stays on this device in the beta.',
  'Scan does not move or delete original clips.',
  'Delete means Move to Mimir Trash.',
  'You can manually set status per incident.',
]

function StepIndicator({ currentStep }: { currentStep: number }) {
  return (
    <div className="flex flex-wrap gap-2">
      {steps.map((step, index) => (
        <div
          key={step}
          className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-[12px] transition ${
            index === currentStep
              ? 'border-white/24 bg-white/[0.08] text-[var(--mimir-text)]'
              : index < currentStep
                ? 'border-white/[0.09] bg-white/[0.035] text-[var(--mimir-text-muted)]'
                : 'border-white/[0.055] bg-black/10 text-[var(--mimir-text-subtle)]'
          }`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              index === currentStep
                ? 'bg-[var(--mimir-text)]'
                : index < currentStep
                  ? 'bg-[var(--mimir-status-green)]'
                  : 'bg-white/18'
            }`}
          />
          {step}
        </div>
      ))}
    </div>
  )
}

export function OnboardingFlow({ initialScanMode, onComplete }: OnboardingFlowProps) {
  const [step, setStep] = useState(0)
  const [selectedScanMode, setSelectedScanMode] = useState<ScanMode>(initialScanMode)
  const isLastStep = step === steps.length - 1

  const goNext = () => {
    if (isLastStep) {
      onComplete(selectedScanMode, true)
      return
    }

    setStep(value => Math.min(steps.length - 1, value + 1))
  }

  return (
    <main className="mx-auto flex min-h-[calc(100vh-32px)] w-full max-w-[1180px] flex-col overflow-hidden rounded-xl border border-[var(--mimir-border)] bg-[radial-gradient(circle_at_50%_0%,rgba(255,255,255,0.055),transparent_34%),var(--mimir-bg-depth)] shadow-[0_30px_100px_rgba(0,0,0,0.56)] sm:min-h-[calc(100vh-48px)]">
      <header className="flex min-h-[78px] items-center justify-between gap-4 px-5 sm:px-7">
        <div className="flex items-center gap-3">
          <img src={mimirMark} alt="" className="h-9 w-9 object-contain opacity-95" />
          <div>
            <div className="text-[15px] font-semibold text-[var(--mimir-text)]">Mimir</div>
            <div className="text-[12px] text-[var(--mimir-text-subtle)]">Private beta setup</div>
          </div>
        </div>
        <StepIndicator currentStep={step} />
      </header>

      <section className="grid flex-1 gap-8 px-5 pb-7 pt-4 sm:px-8 lg:grid-cols-[minmax(0,1fr)_360px] lg:px-12">
        <div className="flex min-h-[560px] flex-col justify-center">
          {step === 0 && (
            <div>
              <div className="mb-6 text-[12px] font-medium uppercase tracking-[0.24em] text-[var(--mimir-text-subtle)]">
                Welcome
              </div>
              <h1 className="text-[64px] font-semibold leading-[0.95] text-[var(--mimir-text)] sm:text-[84px]">
                Mimir
              </h1>
              <p className="mt-6 max-w-[720px] text-[30px] font-semibold leading-tight text-[var(--mimir-text)] sm:text-[42px]">
                Find the moments worth watching.
              </p>
              <p className="mt-6 max-w-[610px] text-[17px] leading-8 text-[var(--mimir-text-muted)]">
                AI incident review for vehicle footage. Scan locally, review quickly, and stay in control of your clips.
              </p>
            </div>
          )}

          {step === 1 && (
            <div>
              <div className="mb-6 text-[12px] font-medium uppercase tracking-[0.24em] text-[var(--mimir-text-subtle)]">
                Privacy and control
              </div>
              <h1 className="max-w-[760px] text-[46px] font-semibold leading-[1.02] text-[var(--mimir-text)] sm:text-[64px]">
                Local first, by default.
              </h1>
              <div className="mt-8 grid max-w-[760px] gap-3">
                {privacyNotes.map((note, index) => (
                  <div
                    key={note}
                    className="flex items-start gap-4 rounded-xl border border-white/[0.06] bg-white/[0.026] p-4"
                  >
                    <span
                      className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${
                        index === 2 ? 'bg-[var(--mimir-status-red)]' : 'bg-[var(--mimir-status-green)]'
                      }`}
                    />
                    <span className="text-[15px] leading-6 text-[var(--mimir-text-muted)]">{note}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {step === 2 && (
            <div>
              <div className="mb-6 text-[12px] font-medium uppercase tracking-[0.24em] text-[var(--mimir-text-subtle)]">
                Default scan mode
              </div>
              <h1 className="max-w-[760px] text-[46px] font-semibold leading-[1.02] text-[var(--mimir-text)] sm:text-[64px]">
                Choose how carefully Mimir should look.
              </h1>
              <p className="mt-5 max-w-[620px] text-[16px] leading-7 text-[var(--mimir-text-muted)]">
                You can change this any time before starting a scan.
              </p>

              <div className="mt-8 grid max-w-[850px] gap-3 sm:grid-cols-3">
                {scanModeOptions.map(option => {
                  const active = selectedScanMode === option.value

                  return (
                    <button
                      key={option.value}
                      onClick={() => setSelectedScanMode(option.value)}
                      className={`min-h-[152px] rounded-xl border p-4 text-left transition ${
                        active
                          ? 'border-white/26 bg-white/[0.075] shadow-[0_18px_56px_rgba(0,0,0,0.24)]'
                          : 'border-white/[0.06] bg-white/[0.022] hover:border-white/14 hover:bg-white/[0.04]'
                      }`}
                    >
                      <span className="flex items-center justify-between gap-3">
                        <span className="text-[17px] font-semibold text-[var(--mimir-text)]">{option.title}</span>
                        <span
                          className={`grid h-5 w-5 place-items-center rounded-full border ${
                            active ? 'border-white/70' : 'border-white/18'
                          }`}
                        >
                          {active && <span className="h-2.5 w-2.5 rounded-full bg-[var(--mimir-text)]" />}
                        </span>
                      </span>
                      <span className="mt-4 block text-[13px] leading-6 text-[var(--mimir-text-muted)]">
                        {option.description}
                      </span>
                    </button>
                  )
                })}
              </div>
            </div>
          )}

          {step === 3 && (
            <div>
              <div className="mb-6 text-[12px] font-medium uppercase tracking-[0.24em] text-[var(--mimir-text-subtle)]">
                Ready
              </div>
              <h1 className="max-w-[740px] text-[48px] font-semibold leading-[1.02] text-[var(--mimir-text)] sm:text-[70px]">
                Start with a footage folder.
              </h1>
              <p className="mt-6 max-w-[610px] text-[17px] leading-8 text-[var(--mimir-text-muted)]">
                Mimir will open the import screen and ask you to choose a folder. Your default scan mode is set to{' '}
                <span className="text-[var(--mimir-text)]">
                  {scanModeOptions.find(option => option.value === selectedScanMode)?.title}
                </span>
                .
              </p>
            </div>
          )}
        </div>

        <aside className="flex flex-col justify-between rounded-[22px] border border-white/[0.065] bg-[linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.014))] p-5 shadow-[0_24px_72px_rgba(0,0,0,0.28)]">
          <div>
            <div className="mb-8 h-1 w-12 rounded-full bg-white/22" />
            <div className="text-[13px] uppercase tracking-[0.18em] text-[var(--mimir-text-subtle)]">
              Setup
            </div>
            <p className="mt-4 text-[15px] leading-7 text-[var(--mimir-text-muted)]">
              A short first-run setup so testers understand how Mimir handles footage before scanning.
            </p>
          </div>

          <div className="mt-8">
            <div className="mb-4 flex gap-2">
              {steps.map((item, index) => (
                <div
                  key={item}
                  className={`h-1.5 flex-1 rounded-full ${index <= step ? 'bg-white/50' : 'bg-white/[0.08]'}`}
                />
              ))}
            </div>
            <div className="flex gap-2">
              {step > 0 && (
                <button
                  onClick={() => setStep(value => Math.max(0, value - 1))}
                  className="h-11 rounded-lg bg-white/[0.04] px-4 text-[13px] font-medium text-[var(--mimir-text-muted)] transition hover:bg-white/[0.07] hover:text-[var(--mimir-text)]"
                >
                  Back
                </button>
              )}
              <button
                onClick={goNext}
                className="h-11 flex-1 rounded-lg bg-[var(--mimir-text)] px-5 text-[14px] font-semibold text-black shadow-[0_16px_38px_rgba(255,255,255,0.075)] transition hover:bg-white"
              >
                {isLastStep ? 'Choose footage folder' : 'Continue'}
              </button>
            </div>
            {!isLastStep && (
              <button
                onClick={() => onComplete(selectedScanMode, false)}
                className="mt-3 h-10 w-full rounded-lg text-[12px] font-medium text-[var(--mimir-text-subtle)] transition hover:bg-white/[0.035] hover:text-[var(--mimir-text-muted)]"
              >
                Skip setup
              </button>
            )}
          </div>
        </aside>
      </section>
    </main>
  )
}
