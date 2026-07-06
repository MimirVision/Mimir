import { Component, type ErrorInfo, type ReactNode } from 'react'
import { invoke } from '@tauri-apps/api/tauri'
import { MIMIR_VERSION } from '../config'

interface CrashSafeBoundaryProps {
  children: ReactNode
  incidentId?: string
  attemptedVideoPath?: string
  onBack?: () => void
  onOpenFolder?: (path: string) => void
  title?: string
}

interface CrashSafeBoundaryState {
  error: Error | null
  stack: string
  copied: boolean
}

function stringifyError(error: unknown) {
  if (error instanceof Error) {
    return error.message
  }

  return String(error)
}

export function diagnosticsText({
  incidentId,
  attemptedVideoPath,
  errorMessage,
  stackTrace,
}: {
  incidentId?: string
  attemptedVideoPath?: string
  errorMessage?: string
  stackTrace?: string
}) {
  return [
    `timestamp: ${new Date().toISOString()}`,
    `mimir version: ${MIMIR_VERSION}`,
    `incident id: ${incidentId || 'unknown'}`,
    `attempted video path: ${attemptedVideoPath || 'not available'}`,
    `error message: ${errorMessage || 'not available'}`,
    `stack trace: ${stackTrace || 'not available'}`,
  ].join('\n')
}

export async function logIncidentDiagnostic({
  incidentId,
  attemptedVideoPath,
  errorMessage,
  stackTrace,
}: {
  incidentId?: string
  attemptedVideoPath?: string
  errorMessage?: string
  stackTrace?: string
}) {
  try {
    await invoke('log_incident_diagnostic', {
      incidentId: incidentId || 'unknown',
      attemptedVideoPath: attemptedVideoPath || '',
      errorMessage: errorMessage || 'Unknown error',
      stackTrace: stackTrace || '',
    })
  } catch {
    // Diagnostics must never become the crash path.
  }
}

async function copyText(value: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value)
    return
  }

  const textArea = document.createElement('textarea')
  textArea.value = value
  textArea.style.position = 'fixed'
  textArea.style.left = '-9999px'
  document.body.appendChild(textArea)
  textArea.focus()
  textArea.select()
  document.execCommand('copy')
  document.body.removeChild(textArea)
}

export class CrashSafeBoundary extends Component<CrashSafeBoundaryProps, CrashSafeBoundaryState> {
  state: CrashSafeBoundaryState = {
    error: null,
    stack: '',
    copied: false,
  }

  static getDerivedStateFromError(error: Error): Partial<CrashSafeBoundaryState> {
    return { error, copied: false }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    const stack = info.componentStack || error.stack || ''
    this.setState({ stack })
    void logIncidentDiagnostic({
      incidentId: this.props.incidentId,
      attemptedVideoPath: this.props.attemptedVideoPath,
      errorMessage: stringifyError(error),
      stackTrace: stack,
    })
  }

  componentDidUpdate(previousProps: CrashSafeBoundaryProps) {
    if (
      this.state.error &&
      (previousProps.incidentId !== this.props.incidentId ||
        previousProps.attemptedVideoPath !== this.props.attemptedVideoPath)
    ) {
      this.setState({ error: null, stack: '', copied: false })
    }
  }

  copyDiagnostics = async () => {
    const { error, stack } = this.state
    const text = diagnosticsText({
      incidentId: this.props.incidentId,
      attemptedVideoPath: this.props.attemptedVideoPath,
      errorMessage: error ? stringifyError(error) : 'Unknown error',
      stackTrace: stack || error?.stack || '',
    })

    try {
      await copyText(text)
      this.setState({ copied: true })
    } catch {
      this.setState({ copied: false })
    }
  }

  render() {
    if (!this.state.error) {
      return this.props.children
    }

    const attemptedVideoPath = this.props.attemptedVideoPath || ''

    return (
      <section className="rounded-[18px] border border-red-300/20 bg-red-500/10 p-5 text-red-50 shadow-[0_18px_44px_rgba(0,0,0,0.18)]">
        <div className="text-[12px] font-medium uppercase tracking-[0.18em] text-red-100/70">
          {this.props.title || 'Incident error'}
        </div>
        <h2 className="mt-3 text-[22px] font-semibold text-red-50">Mimir could not open this incident.</h2>
        <p className="mt-2 text-[13px] leading-6 text-red-100/78">
          The rest of the app is still running. Diagnostics were written to Documents\Mimir Logs\app_crash_log.txt.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          {this.props.onBack && (
            <button
              type="button"
              onClick={this.props.onBack}
              className="h-10 rounded-lg bg-white px-4 text-[13px] font-semibold text-black transition hover:bg-red-50"
            >
              Back to review
            </button>
          )}
          <button
            type="button"
            onClick={() => void this.copyDiagnostics()}
            className="h-10 rounded-lg border border-red-100/25 bg-white/[0.06] px-4 text-[13px] font-medium text-red-50 transition hover:bg-white/[0.1]"
          >
            {this.state.copied ? 'Diagnostics copied' : 'Copy diagnostics'}
          </button>
          {attemptedVideoPath && this.props.onOpenFolder && (
            <button
              type="button"
              onClick={() => this.props.onOpenFolder?.(attemptedVideoPath)}
              className="h-10 rounded-lg border border-red-100/25 bg-white/[0.06] px-4 text-[13px] font-medium text-red-50 transition hover:bg-white/[0.1]"
            >
              Open folder
            </button>
          )}
        </div>
      </section>
    )
  }
}
