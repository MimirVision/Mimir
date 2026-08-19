import { invoke } from '@tauri-apps/api/core'
import type {
  CollectionDetail,
  CollectionListItem,
  FeedbackCategory,
  FeedbackDetail,
  FeedbackListItem,
  FeedbackReport,
  FeedbackReview,
  LabelQueue,
  LabelSaveResult,
  GateProgress,
  ReportSummary,
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
  getFeedbackReviews: () => invoke<Record<string, FeedbackReview>>('get_feedback_reviews'),
  saveFeedbackReview: (packageId: string, reviewed: boolean, note: string, category: FeedbackCategory) =>
    invoke<void>('save_feedback_review', { packageId, reviewed, note, category }),
  generateFeedbackReport: () => invoke<FeedbackReport>('generate_feedback_report'),
  listRecentReports: () => invoke<ReportSummary[]>('list_recent_reports'),
  openReportFile: (path: string) => invoke<void>('open_report_file', { path }),
  listCollections: () => invoke<{ items: CollectionListItem[] }>('list_collections'),
  showCollection: (packageId: string) => invoke<CollectionDetail>('show_collection', { packageId }),
  openInCvat: (taskId: number) => invoke<void>('open_in_cvat', { taskId }),
  listLabelCandidates: (limit: number) => invoke<LabelQueue>('list_label_candidates', { limit }),
  saveLabel: (group: string, severity: string, category: string, notes: string, sourceSet: string) =>
    invoke<LabelSaveResult>('save_label', { group, severity, category, notes, sourceSet }),
}
