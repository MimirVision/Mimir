import { FEEDBACK_EMAIL } from '../config'

interface BetaPrivacyNoticeProps {
  open: boolean
  onAccept: () => void
}

interface BetaNoticeFooterProps {
  onOpen: () => void
}

export function BetaPrivacyNotice({ open, onAccept }: BetaPrivacyNoticeProps) {
  if (!open) {
    return null
  }

  return (
    <div className="fixed inset-0 z-[80] grid place-items-center bg-black/74 p-4 backdrop-blur-sm">
      <section className="w-full max-w-[560px] rounded-[24px] border border-white/[0.08] bg-[radial-gradient(circle_at_50%_0%,rgba(255,255,255,0.06),transparent_34%),var(--mimir-bg-depth)] p-6 shadow-[0_34px_110px_rgba(0,0,0,0.68)] sm:p-7">
        <div className="mb-5 h-1 w-12 rounded-full bg-white/24" />
        <div className="text-[12px] font-medium uppercase tracking-[0.22em] text-[var(--mimir-text-subtle)]">
          Mimir Free Beta
        </div>
        <h2 className="mt-4 text-[30px] font-semibold leading-tight text-[var(--mimir-text)]">
          Review carefully. Stay in control.
        </h2>

        <div className="mt-5 space-y-4 text-[14px] leading-7 text-[var(--mimir-text-muted)]">
          <p>
            Mimir is still learning. Always review important footage yourself before deleting or moving clips.
          </p>
          <p>
            Your footage stays under your control. Scanning is local-first, and Mimir does not move or delete
            clips during scan. You decide what to move to your Library or Mimir Trash.
          </p>
          <p>
            Mimir may misclassify events. Use the feedback export feature to report mistakes.
          </p>
          {/* The beta docs tell testers to email this, but nothing in the app
              ever showed it -- a tester who never opens README_START_HERE.html
              had no in-app way to reach anyone. */}
          <p>
            Something wrong that feedback cannot capture? Email{' '}
            <a
              href={`mailto:${FEEDBACK_EMAIL}`}
              className="font-semibold text-[var(--mimir-text)] underline decoration-white/30 underline-offset-2 hover:decoration-white/60"
            >
              {FEEDBACK_EMAIL}
            </a>
            . Please don't post footage on forums or social media.
          </p>
        </div>

        <button
          onClick={onAccept}
          className="mt-7 h-12 w-full rounded-xl bg-[var(--mimir-text)] px-5 text-[14px] font-semibold text-black shadow-[0_18px_42px_rgba(255,255,255,0.08)] transition hover:bg-white"
        >
          I understand
        </button>
      </section>
    </div>
  )
}

export function BetaNoticeFooter({ onOpen }: BetaNoticeFooterProps) {
  return (
    <footer className="pointer-events-none fixed inset-x-0 bottom-3 z-40 flex justify-center px-4">
      <button
        onClick={onOpen}
        className="pointer-events-auto rounded-full border border-white/[0.075] bg-black/38 px-3.5 py-2 text-[11px] font-medium text-[var(--mimir-text-subtle)] shadow-[0_12px_34px_rgba(0,0,0,0.32)] backdrop-blur-md transition hover:border-white/14 hover:bg-black/52 hover:text-[var(--mimir-text-muted)]"
      >
        Beta notice
      </button>
    </footer>
  )
}
