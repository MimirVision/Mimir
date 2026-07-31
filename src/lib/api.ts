import { invoke } from '@tauri-apps/api/core'
import type {
  CollectionDetail,
  CollectionListItem,
  FeedbackDetail,
  FeedbackListItem,
  GateProgress,
  SecretField,
  Settings,
  SettingsView,
  SyncOutcome,
} from './types'

export const api = {
  getSettings: () => invoke<SettingsView>('get_settings'),
  saveSettings: (settings: Settings) => invoke<void>('save_settings', { settings }),
  saveSecret: (field: SecretField, value: string) => invoke<void>('save_secret', { field, value }),
  clearSecret: (field: SecretField) => invoke<void>('clear_secret', { field }),
  runSync: () => invoke<SyncOutcome>('run_sync'),
  getStatus: () => invoke<GateProgress>('get_status'),
  listFeedback: () => invoke<{ items: FeedbackListItem[] }>('list_feedback'),
  showFeedback: (packageId: string) => invoke<FeedbackDetail>('show_feedback', { packageId }),
  listCollections: () => invoke<{ items: CollectionListItem[] }>('list_collections'),
  showCollection: (packageId: string) => invoke<CollectionDetail>('show_collection', { packageId }),
  openInCvat: (taskId: number) => invoke<void>('open_in_cvat', { taskId }),
}
