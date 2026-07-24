import type { MimirIncident } from '../types'

export function formatDateTime(value?: string | null) {
  if (!value) {
    return ''
  }

  const date = new Date(value)

  if (Number.isNaN(date.getTime())) {
    return value
  }

  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function sourceEventReason(incident: MimirIncident) {
  return incident.source_event_reason || incident.tesla_event_reason || ''
}

export function sourceEventTimestamp(incident: MimirIncident) {
  return incident.source_event_timestamp || incident.tesla_event_timestamp || incident.created_at
}

export function formatEventType(value?: string) {
  if (!value) {
    return 'Unclassified moment'
  }

  return value
    .split('_')
    .filter(Boolean)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

export function sourceFilename(value?: string) {
  if (!value) {
    return ''
  }

  const parts = value.split(/[\\/]/)
  return parts[parts.length - 1] || value
}
