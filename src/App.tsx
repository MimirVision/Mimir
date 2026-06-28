import { useEffect, useMemo, useState } from 'react'
import { AssessmentPanel } from './components/AssessmentPanel'
import { ImportPanel } from './components/ImportPanel'
import { IncidentTimeline } from './components/IncidentTimeline'
import { IncidentViewer } from './components/IncidentViewer'
import { ScanProgress } from './components/ScanProgress'
import { SessionHeader } from './components/SessionHeader'
import { Sidebar } from './components/Sidebar'
import { mockIncidents, mockSession, scanSteps } from './mockData'
import type { AppMode } from './types'

const mockFolder = 'Tesla USB / TeslaCam / June 26'

function PreviewSwitcher({
  mode,
  onModeChange,
}: {
  mode: AppMode
  onModeChange: (mode: AppMode) => void
}) {
  return (
    <div className="fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 rounded-full border border-[var(--mimir-border)] bg-[var(--mimir-surface)] p-1 shadow-[0_18px_50px_rgba(0,0,0,0.45)]">
      {[
        ['empty', 'Empty'],
        ['scanning', 'Scanning'],
        ['review', 'Review'],
      ].map(([value, label]) => (
        <button
          key={value}
          onClick={() => onModeChange(value as AppMode)}
          className={`h-8 rounded-full px-4 text-[12px] font-medium transition ${
            mode === value ? 'bg-[var(--mimir-text)] text-black' : 'text-white/48 hover:text-white'
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

export default function App() {
  const [mode, setMode] = useState<AppMode>('empty')
  const [selectedFolder, setSelectedFolder] = useState('')
  const [selectedIncidentId, setSelectedIncidentId] = useState(mockIncidents[0].id)
  const [scanTick, setScanTick] = useState(0)

  const selectedIncident = useMemo(
    () => mockIncidents.find(incident => incident.id === selectedIncidentId) ?? mockIncidents[0],
    [selectedIncidentId],
  )

  const activeStep = mode === 'scanning' ? Math.floor(scanTick / 20) % scanSteps.length : 0
  const progress = mode === 'scanning' ? Math.min(96, 8 + scanTick) : 0

  useEffect(() => {
    if (mode !== 'scanning') return
    setScanTick(0)
    const timer = window.setInterval(() => {
      setScanTick(current => (current >= 88 ? 12 : current + 4))
    }, 700)
    return () => window.clearInterval(timer)
  }, [mode])

  const chooseFolder = () => {
    setSelectedFolder(mockFolder)
  }

  const startAnalysis = () => {
    if (!selectedFolder) return
    setMode('scanning')
  }

  const engineLabel = mode === 'scanning' ? 'Engine analyzing' : 'Local engine ready'

  return (
    <div className="min-h-screen overflow-hidden bg-[var(--mimir-bg)] text-[var(--mimir-text)]">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_18%_0%,rgba(255,255,255,0.055),transparent_28%),linear-gradient(135deg,rgba(255,255,255,0.035),transparent_38%)]" />
      <div className="relative flex h-screen p-4">
        <div className="mx-auto flex h-full w-full max-w-[1520px] overflow-hidden rounded-xl border border-[var(--mimir-border)] bg-[var(--mimir-bg-depth)] shadow-[0_28px_90px_rgba(0,0,0,0.5)]">
          <Sidebar mode={mode} engineLabel={engineLabel} />

          <main className="min-w-0 flex-1">
            {mode === 'empty' && (
              <ImportPanel
                selectedFolder={selectedFolder}
                onChooseFolder={chooseFolder}
                onAnalyze={startAnalysis}
              />
            )}

            {mode === 'scanning' && (
              <ScanProgress
                steps={scanSteps}
                activeStep={activeStep}
                progress={progress}
                folderName={selectedFolder || mockFolder}
              />
            )}

            {mode === 'review' && (
              <div className="grid h-full min-h-0 grid-cols-[minmax(0,1fr)_360px]">
                <div className="min-w-0 overflow-y-auto">
                  <SessionHeader session={mockSession} />
                  <div className="px-7 pb-7">
                    <IncidentViewer incident={selectedIncident} />
                    <IncidentTimeline
                      incidents={mockIncidents}
                      selectedIncidentId={selectedIncident.id}
                      onSelectIncident={setSelectedIncidentId}
                    />
                  </div>
                </div>
                <AssessmentPanel incident={selectedIncident} />
              </div>
            )}
          </main>
        </div>
      </div>

      <PreviewSwitcher mode={mode} onModeChange={setMode} />
    </div>
  )
}
