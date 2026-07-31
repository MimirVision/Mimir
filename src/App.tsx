import { useEffect, useState } from 'react'
import { api } from './lib/api'
import { SettingsScreen } from './screens/SettingsScreen'
import { DashboardScreen } from './screens/DashboardScreen'
import { FeedbackScreen } from './screens/FeedbackScreen'
import { CollectionsScreen } from './screens/CollectionsScreen'

type Tab = 'dashboard' | 'feedback' | 'collections' | 'settings'

const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'feedback', label: 'Feedback' },
  { id: 'collections', label: 'Collections' },
  { id: 'settings', label: 'Settings' },
]

export default function App() {
  const [configured, setConfigured] = useState<boolean | null>(null)
  const [tab, setTab] = useState<Tab>('dashboard')

  const checkConfigured = () => {
    api
      .getSettings()
      .then(view => {
        const ready = Boolean(view.dataset_root && view.inbox && view.feedback_inbox && view.identity_path)
        setConfigured(ready)
        if (!ready) setTab('settings')
      })
      .catch(() => setConfigured(false))
  }

  useEffect(checkConfigured, [])

  return (
    <div className="mimir-page-glow min-h-screen">
      <header className="sticky top-0 z-10 border-b border-mimir-border bg-mimir-bg/85 backdrop-blur">
        <div className="mx-auto flex max-w-4xl items-center gap-1 px-6 py-3">
          <span className="mr-4 text-[13px] font-semibold tracking-tight text-mimir-text">Mimir Forge</span>
          {TABS.map(item => (
            <button
              key={item.id}
              type="button"
              onClick={() => setTab(item.id)}
              className={`rounded-md px-3 py-1.5 text-[12px] font-medium ${
                tab === item.id
                  ? 'bg-mimir-accent-soft text-mimir-accent'
                  : 'text-mimir-text-muted hover:text-mimir-text'
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
      </header>

      <main>
        {tab === 'dashboard' && <DashboardScreen ready={configured === true} />}
        {tab === 'feedback' && <FeedbackScreen ready={configured === true} />}
        {tab === 'collections' && <CollectionsScreen ready={configured === true} />}
        {tab === 'settings' && <SettingsScreen onSaved={checkConfigured} />}
      </main>
    </div>
  )
}
