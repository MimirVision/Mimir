import { useEffect, useState } from 'react'
import { api } from './lib/api'
import mark from './assets/mark.png'
import { SettingsScreen } from './screens/SettingsScreen'
import { DashboardScreen } from './screens/DashboardScreen'
import { FeedbackScreen } from './screens/FeedbackScreen'
import { CollectionsScreen } from './screens/CollectionsScreen'
import { LabelScreen } from './screens/LabelScreen'

type Tab = 'dashboard' | 'labelling' | 'feedback' | 'collections' | 'settings'

const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'labelling', label: 'Labelling' },
  { id: 'feedback', label: 'Feedback' },
  { id: 'collections', label: 'Collections' },
  { id: 'settings', label: 'Settings' },
]

export default function App() {
  const [configured, setConfigured] = useState<boolean | null>(null)
  const [tab, setTab] = useState<Tab>('dashboard')
  const [feedbackFocusId, setFeedbackFocusId] = useState<string | null>(null)

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

  const goToFeedback = (packageId?: string) => {
    setFeedbackFocusId(packageId ?? null)
    setTab('feedback')
  }

  return (
    <div className="mimir-page-glow min-h-screen">
      <header className="sticky top-0 z-10 border-b border-mimir-border bg-mimir-bg/85 backdrop-blur">
        <div className="mx-auto flex max-w-4xl items-center gap-1 px-6 py-3">
          <img src={mark} alt="" className="mr-2 h-5 w-5 opacity-90" />
          <span className="mr-4 text-[13px] font-semibold tracking-tight text-mimir-text">Mimir Forge</span>
          {TABS.map(item => (
            <button
              key={item.id}
              type="button"
              onClick={() => setTab(item.id)}
              className={`rounded-md px-3 py-1.5 text-[12px] font-medium transition-colors duration-150 ${
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
        {tab === 'dashboard' && <DashboardScreen ready={configured === true} onReviewFeedback={goToFeedback} />}
        {tab === 'feedback' && (
          <FeedbackScreen
            ready={configured === true}
            focusId={feedbackFocusId}
            onFocusConsumed={() => setFeedbackFocusId(null)}
          />
        )}
        {tab === 'labelling' && <LabelScreen sourceSet="local_scan_set" />}
        {tab === 'collections' && <CollectionsScreen ready={configured === true} />}
        {tab === 'settings' && <SettingsScreen onSaved={checkConfigured} />}
      </main>
    </div>
  )
}
