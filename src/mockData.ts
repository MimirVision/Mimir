import type { Incident, ScanStep, SessionSummary } from './types'

export const mockSession: SessionSummary = {
  date: 'June 26, 2026',
  time: '9:41 AM',
  source: 'Tesla USB',
  clipCount: 284,
  storage: '17 GB',
  incidentCount: 2,
}

export const scanSteps: ScanStep[] = [
  {
    id: 'select-footage',
    label: 'Select footage',
    description: 'Choose the TeslaCam or dashcam folder from your drive.',
  },
  {
    id: 'confirm-settings',
    label: 'Confirm scan settings',
    description: 'Review what Mimir will scan before analysis begins.',
  },
  {
    id: 'analyze',
    label: 'Analyze',
    description: 'Process footage locally and prepare relevant moments.',
  },
  {
    id: 'review-results',
    label: 'Review results',
    description: 'Open the moments that need attention.',
  },
]

export const mockIncidents: Incident[] = [
  {
    id: 'door-interaction',
    title: 'Possible Door Interaction',
    summary:
      "A person approached the driver's side, stayed near the vehicle, and appeared to reach toward the door area before leaving.",
    assessment: 'High likelihood of vehicle interaction',
    severity: 'high',
    duration: '18 sec',
    camera: 'Left Repeater',
    objects: ['Person', 'Vehicle'],
    clipLabel: '2026-06-26_02-13-10-left_repeater.mp4',
    evidenceNotes: [
      'Person detected close to the driver-side door.',
      'Movement remained near the vehicle for several sampled frames.',
      'Best evidence frame shows activity beside the door area.',
    ],
    moments: [
      {
        id: 'approach',
        time: '02:13:10',
        title: 'Approaches vehicle',
        description: 'Person enters the camera view and moves toward the driver side.',
        tone: 'review',
      },
      {
        id: 'door',
        time: '02:13:16',
        title: 'Stops at door',
        description: 'Person pauses near the handle area.',
        tone: 'review',
      },
      {
        id: 'interaction',
        time: '02:13:22',
        title: 'Possible interaction',
        description: 'Arm movement appears directed toward the door.',
        tone: 'high',
      },
      {
        id: 'leaves',
        time: '02:13:28',
        title: 'Leaves area',
        description: 'Person exits the immediate vehicle area.',
        tone: 'ignore',
      },
    ],
  },
  {
    id: 'vehicle-nearby',
    title: 'Vehicle Stopped Nearby',
    summary:
      'A vehicle stopped close to the parked car for a short period. No person interaction was detected in the sampled frames.',
    assessment: 'Review recommended',
    severity: 'review',
    duration: '24 sec',
    camera: 'Front',
    objects: ['Vehicle'],
    clipLabel: '2026-06-26_03-47-00-front.mp4',
    evidenceNotes: [
      'Vehicle detected in the monitored area.',
      'Movement remained close enough to keep the clip for review.',
      'No clear person-to-vehicle interaction was detected.',
    ],
    moments: [
      {
        id: 'arrival',
        time: '03:47:00',
        title: 'Vehicle enters',
        description: 'Nearby vehicle moves into view.',
        tone: 'review',
      },
      {
        id: 'stop',
        time: '03:47:08',
        title: 'Stops nearby',
        description: 'Vehicle remains near the parked car.',
        tone: 'review',
      },
      {
        id: 'clear',
        time: '03:47:24',
        title: 'Area clears',
        description: 'No direct interaction is visible.',
        tone: 'ignore',
      },
    ],
  },
]
