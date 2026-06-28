import { useState } from 'react'
import { open } from '@tauri-apps/api/dialog'
import { readTextFile } from '@tauri-apps/api/fs'
import { AssessmentPanel } from './components/AssessmentPanel'
import { ImportPanel } from './components/ImportPanel'
import { IncidentTimeline } from './components/IncidentTimeline'
import { IncidentViewer } from './components/IncidentViewer'
import { LatestSessionResults } from './components/LatestSessionResults'
import { SessionHeader } from './components/SessionHeader'
import { Sidebar } from './components/Sidebar'
import { mockIncidents, mockSession } from './mockData'
import type { AppMode, MimirSession, SessionLoadState } from './types'

const latestSessionPath = 'C:\\Mimir_Backend\\MimirOutput\\latest_session.json'

export default function App() {
  const [mode, setMode] = useState<AppMode>('empty')
  const [selectedFolder, setSelectedFolder] = useState('')
  const [selectedIncidentId, setSelectedIncidentId] = useState(mockIncidents[0].id)
  const [latestSession, setLatestSession] = useState<MimirSession | null>(null)
  const [sessionLoadState, setSessionLoadState] = useState<SessionLoadState>('idle')

  const selectedIncident =
    mockIncidents.find(incident => incident.id === selectedIncidentId) ?? mockIncidents[0]

  const chooseFolder = async () => {
    const selected = await open({
      directory: true,
      multiple: false,
      title: 'Choose TeslaCam Folder',
    })

    if (typeof selected === 'string') {
      setSelectedFolder(selected)
    }
  }

  const loadLatestSession = async () => {
    setSessionLoadState('loading')
    setLatestSession(null)

    try {
      const contents = await readTextFile(latestSessionPath)

      try {
        const parsed = JSON.parse(contents) as MimirSession

        if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.incidents)) {
          throw new Error('Invalid Mimir session shape')
        }

        setLatestSession(parsed)
        setSessionLoadState('loaded')
        setMode('results')
      } catch {
        setSessionLoadState('error')
        setMode('empty')
      }
    } catch {
      setSessionLoadState('missing')
      setMode('empty')
    }
  }

  const showSamplePreview = () => {
    setMode('sample')
  }

  const engineLabel = 'Local engine ready'

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
                onLoadLatestSession={loadLatestSession}
                onPreviewSample={showSamplePreview}
                loadState={sessionLoadState}
              />
            )}

            {mode === 'results' && (
              <LatestSessionResults
                loadState={sessionLoadState}
                session={latestSession}
                onLoad={loadLatestSession}
              />
            )}

            {mode === 'sample' && (
              <div className="grid h-full min-h-0 grid-cols-[minmax(0,1fr)_360px]">
                <div className="min-w-0 overflow-y-auto">
                  <div className="border-b border-[var(--mimir-border)] bg-amber-400/10 px-7 py-3 text-[13px] font-medium text-amber-100">
                    Developer preview only. This screen uses sample data and is not a real scan result.
                  </div>
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
    </div>
  )
}
