import type { AppMode } from '../types'
import mimirMark from '../assets/mimir-mark.png'

interface SidebarProps {
  mode: AppMode
  engineLabel: string
}

export function Sidebar({ mode, engineLabel }: SidebarProps) {
  const navItems = [
    { label: 'Sessions', active: mode !== 'empty' },
    { label: 'Reports', active: false },
    { label: 'Settings', active: false },
  ]

  return (
    <aside className="flex w-[216px] shrink-0 flex-col border-r border-[var(--mimir-border)] bg-[var(--mimir-bg-depth)] px-4 py-5">
      <div className="mb-8 flex items-center gap-3 px-2">
        <div className="grid h-10 w-10 place-items-center rounded-lg border border-[var(--mimir-border-strong)] bg-black/35">
          <img src={mimirMark} alt="" className="h-7 w-7 object-contain" />
        </div>
        <div>
          <div className="text-[15px] font-semibold text-white">Mimir</div>
          <div className="text-[12px] text-[var(--mimir-text-muted)]">Local Sentry review</div>
        </div>
      </div>

      <nav className="space-y-1">
        {navItems.map(item => (
          <button
            key={item.label}
            className={`flex h-11 w-full items-center gap-3 rounded-lg px-3 text-left text-[13px] transition ${
              item.active
                ? 'bg-white/[0.07] text-white shadow-[inset_0_0_0_1px_rgba(255,255,255,0.06)]'
                : 'text-white/48 hover:bg-white/[0.045] hover:text-white/78'
            }`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${item.active ? 'bg-white/80' : 'bg-white/18'}`} />
            {item.label}
          </button>
        ))}
      </nav>

      <div className="mt-auto rounded-lg border border-[var(--mimir-border)] bg-black/20 p-3">
        <div className="flex items-center gap-2 text-[12px] font-medium text-white/78">
          <span className="h-2 w-2 rounded-full bg-[var(--mimir-status-green)]" />
          {engineLabel}
        </div>
        <div className="mt-2 text-[12px] leading-5 text-[var(--mimir-text-muted)]">
          Local processing. No upload required.
        </div>
      </div>
    </aside>
  )
}
