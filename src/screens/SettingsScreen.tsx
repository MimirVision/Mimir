import { useEffect, useState } from 'react'
import { open } from '@tauri-apps/plugin-dialog'
import { api } from '../lib/api'
import { describeError, type DescribedError } from '../lib/errorMessages'
import { ErrorNotice } from '../components/ErrorNotice'
import { Spinner } from '../components/Spinner'
import type { SecretField, Settings, SettingsView } from '../lib/types'

const EMPTY_SETTINGS: Settings = {
  dataset_root: '',
  inbox: '',
  feedback_inbox: '',
  identity_path: '',
  r2_endpoint: '',
  r2_bucket: '',
  cvat_url: '',
  create_cvat_tasks: false,
}

interface SecretRowProps {
  label: string
  hint: string
  field: SecretField
  isSet: boolean
  value: string
  onChange: (value: string) => void
  onCleared: () => void
}

// A credential row's Save button used to fire its own request immediately,
// separately from the rest of the form -- which meant filling in Endpoint
// and a credential, clicking only the credential's Save, and never hitting
// the bottom button silently dropped the Endpoint. Now every field here is
// just part of the same form state and goes out together with one Save
// settings click. Clear still acts immediately: there's nothing to batch,
// and instant feedback matters more for a destructive action.
function SecretRow({ label, hint, field, isSet, value, onChange, onCleared }: SecretRowProps) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<DescribedError | null>(null)

  const clear = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.clearSecret(field)
      onCleared()
    } catch (err) {
      setError(describeError(err, `Could not clear ${label}.`))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-lg border border-mimir-border bg-mimir-surface-soft/60 p-3.5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-[13px] font-medium text-mimir-text">{label}</div>
          <div className="mt-0.5 text-[11px] text-mimir-text-subtle">{hint}</div>
        </div>
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${
            isSet ? 'bg-mimir-accent-soft text-mimir-accent' : 'bg-white/5 text-mimir-text-subtle'
          }`}
        >
          {isSet ? 'Saved' : 'Not set'}
        </span>
      </div>
      <div className="mt-2.5 flex gap-2">
        <input
          type="password"
          value={value}
          onChange={event => onChange(event.target.value)}
          placeholder={isSet ? 'Replace saved value, then Save settings below...' : 'Paste value, then Save settings below...'}
          className="min-w-0 flex-1 rounded-md border border-mimir-border bg-mimir-bg-depth px-2.5 py-1.5 text-[12px] text-mimir-text outline-none focus-visible:border-mimir-accent"
        />
        {isSet && (
          <button
            type="button"
            disabled={busy}
            onClick={clear}
            className="shrink-0 rounded-md border border-mimir-border px-3 py-1.5 text-[12px] text-mimir-text-muted disabled:opacity-40"
          >
            Clear
          </button>
        )}
      </div>
      <ErrorNotice error={error} className="mt-2.5" />
    </div>
  )
}

interface PathFieldProps {
  label: string
  hint: string
  value: string
  onChange: (value: string) => void
  pickFile?: boolean
}

function PathField({ label, hint, value, onChange, pickFile = false }: PathFieldProps) {
  const browse = async () => {
    const selection = await open({ directory: !pickFile, multiple: false })
    if (typeof selection === 'string') {
      onChange(selection)
    }
  }

  return (
    <label className="block">
      <div className="text-[13px] font-medium text-mimir-text">{label}</div>
      <div className="mt-0.5 text-[11px] text-mimir-text-subtle">{hint}</div>
      <div className="mt-1.5 flex gap-2">
        <input
          value={value}
          onChange={event => onChange(event.target.value)}
          className="min-w-0 flex-1 rounded-md border border-mimir-border bg-mimir-bg-depth px-2.5 py-1.5 text-[12px] text-mimir-text outline-none focus-visible:border-mimir-accent"
        />
        <button
          type="button"
          onClick={browse}
          className="shrink-0 rounded-md border border-mimir-border-strong bg-mimir-surface-muted px-3 py-1.5 text-[12px] text-mimir-text"
        >
          Browse
        </button>
      </div>
    </label>
  )
}

interface SettingsScreenProps {
  onSaved?: () => void
}

const EMPTY_SECRETS: Record<SecretField, string> = {
  r2_access_key_id: '',
  r2_secret_access_key: '',
  cvat_token: '',
}

export function SettingsScreen({ onSaved }: SettingsScreenProps) {
  const [view, setView] = useState<SettingsView | null>(null)
  const [form, setForm] = useState<Settings>(EMPTY_SETTINGS)
  const [secrets, setSecrets] = useState<Record<SecretField, string>>(EMPTY_SECRETS)
  const [loadError, setLoadError] = useState<DescribedError | null>(null)
  const [saveError, setSaveError] = useState<DescribedError | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const load = async () => {
    try {
      const result = await api.getSettings()
      setView(result)
      setForm({
        dataset_root: result.dataset_root,
        inbox: result.inbox,
        feedback_inbox: result.feedback_inbox,
        identity_path: result.identity_path,
        r2_endpoint: result.r2_endpoint,
        r2_bucket: result.r2_bucket,
        cvat_url: result.cvat_url,
        create_cvat_tasks: result.create_cvat_tasks,
      })
      setLoadError(null)
    } catch (err) {
      setLoadError(describeError(err, 'Could not load settings.'))
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const set = <K extends keyof Settings>(key: K, value: Settings[K]) => {
    setForm(current => ({ ...current, [key]: value }))
    setSaved(false)
  }

  const save = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      await api.saveSettings(form)
      const pending = (Object.entries(secrets) as Array<[SecretField, string]>).filter(([, value]) =>
        value.trim(),
      )
      for (const [field, value] of pending) {
        await api.saveSecret(field, value)
      }
      setSecrets(EMPTY_SECRETS)
      await load()
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
      onSaved?.()
    } catch (err) {
      setSaveError(describeError(err, 'Could not save settings.'))
    } finally {
      setSaving(false)
    }
  }

  const isFirstRun = view !== null && !view.dataset_root

  return (
    <div className="mx-auto max-w-2xl px-6 py-8">
      <h1 className="text-lg font-medium text-mimir-text">Settings</h1>
      {isFirstRun && (
        <p className="mt-1.5 text-[12px] leading-5 text-mimir-text-muted">
          First time here -- fill these in once. Paths point at folders on this machine; credentials are
          stored in Windows Credential Manager, never in plain text.
        </p>
      )}
      <ErrorNotice error={loadError} className="mt-4" />

      <section className="mt-6 space-y-4">
        <h2 className="text-[12px] font-medium uppercase tracking-wide text-mimir-text-subtle">
          Local folders
        </h2>
        <PathField
          label="Dataset root"
          hint="Where intaken contribution collections and the gate progress log live."
          value={form.dataset_root}
          onChange={value => set('dataset_root', value)}
        />
        <PathField
          label="Contribution inbox"
          hint="Downloaded (still-encrypted) .mimir-dataset.age files land here before intake."
          value={form.inbox}
          onChange={value => set('inbox', value)}
        />
        <PathField
          label="Feedback inbox"
          hint="Decrypted feedback packages are filed here after intake."
          value={form.feedback_inbox}
          onChange={value => set('feedback_inbox', value)}
        />
        <PathField
          label="age identity file"
          hint="The private key used to decrypt what testers submitted."
          value={form.identity_path}
          onChange={value => set('identity_path', value)}
          pickFile
        />
      </section>

      <section className="mt-7 space-y-4">
        <h2 className="text-[12px] font-medium uppercase tracking-wide text-mimir-text-subtle">
          R2 (Cloudflare)
        </h2>
        <label className="block">
          <div className="text-[13px] font-medium text-mimir-text">Endpoint</div>
          <input
            value={form.r2_endpoint}
            onChange={event => set('r2_endpoint', event.target.value)}
            placeholder="https://<account-id>.r2.cloudflarestorage.com"
            className="mt-1.5 w-full rounded-md border border-mimir-border bg-mimir-bg-depth px-2.5 py-1.5 text-[12px] text-mimir-text outline-none focus-visible:border-mimir-accent"
          />
        </label>
        <label className="block">
          <div className="text-[13px] font-medium text-mimir-text">Bucket</div>
          <input
            value={form.r2_bucket}
            onChange={event => set('r2_bucket', event.target.value)}
            placeholder="mimir-intake"
            className="mt-1.5 w-full rounded-md border border-mimir-border bg-mimir-bg-depth px-2.5 py-1.5 text-[12px] text-mimir-text outline-none focus-visible:border-mimir-accent"
          />
        </label>
        {view && (
          <>
            <SecretRow
              label="R2 access key ID"
              hint="Developer-only S3 API token with read+list access to the intake bucket."
              field="r2_access_key_id"
              isSet={view.has_r2_access_key_id}
              value={secrets.r2_access_key_id}
              onChange={value => setSecrets(current => ({ ...current, r2_access_key_id: value }))}
              onCleared={load}
            />
            <SecretRow
              label="R2 secret access key"
              hint="Paired with the access key ID above."
              field="r2_secret_access_key"
              isSet={view.has_r2_secret_access_key}
              value={secrets.r2_secret_access_key}
              onChange={value => setSecrets(current => ({ ...current, r2_secret_access_key: value }))}
              onCleared={load}
            />
          </>
        )}
      </section>

      <section className="mt-7 space-y-4">
        <h2 className="text-[12px] font-medium uppercase tracking-wide text-mimir-text-subtle">
          CVAT (optional)
        </h2>
        <label className="block">
          <div className="text-[13px] font-medium text-mimir-text">CVAT URL</div>
          <input
            value={form.cvat_url}
            onChange={event => set('cvat_url', event.target.value)}
            placeholder="http://localhost:8080"
            className="mt-1.5 w-full rounded-md border border-mimir-border bg-mimir-bg-depth px-2.5 py-1.5 text-[12px] text-mimir-text outline-none focus-visible:border-mimir-accent"
          />
        </label>
        <label className="flex items-center gap-2 text-[12px] text-mimir-text-muted">
          <input
            type="checkbox"
            checked={form.create_cvat_tasks}
            onChange={event => set('create_cvat_tasks', event.target.checked)}
          />
          Create CVAT tasks automatically during sync
        </label>
        {view && (
          <SecretRow
            label="CVAT token"
            hint="Used for creating tasks during sync and fetching live task status in Collections."
            field="cvat_token"
            isSet={view.has_cvat_token}
            value={secrets.cvat_token}
            onChange={value => setSecrets(current => ({ ...current, cvat_token: value }))}
            onCleared={load}
          />
        )}
      </section>

      <div className="mt-8 flex items-center gap-3">
        <button
          type="button"
          disabled={saving}
          onClick={save}
          className="rounded-md border border-mimir-accent/40 bg-mimir-accent-soft px-4 py-2 text-[12px] font-medium text-mimir-accent disabled:opacity-60"
        >
          <span className="inline-flex items-center gap-2">
            {saving && <Spinner />}
            {saving ? 'Saving...' : 'Save settings'}
          </span>
        </button>
        <span
          className={`text-[11px] text-mimir-green transition-opacity duration-300 ${saved ? 'opacity-100' : 'opacity-0'}`}
        >
          Saved
        </span>
      </div>
      <ErrorNotice error={saveError} className="mt-3" />
    </div>
  )
}
