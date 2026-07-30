# Mimir Local Evidence Model Card

## Intended Use

Mimir ranks Tesla Sentry incidents as Important, Review, or Ignore and proposes key
moments for human review. It is not intended to prove contact, assign fault, identify
people, or automate insurance/legal decisions.

## Current Pipeline

The pipeline combines sampled-frame motion, localized motion, camera shake, scene
change, temporal object candidates, camera-aware ego-vehicle regions, safety caps,
and dense second-pass timing refinement. It records evidence provenance and timing
uncertainty. Optional local vision-language review is not authoritative.

## Detector Distribution Status

Mimir uses RF-DETR Small exported to ONNX at 512px and runs it through ONNX
Runtime. The exact
model filename, upstream version, SHA-256 checksum, size, class mapping, and Apache
2.0 notice are recorded in `mimir_core_v2/model_manifest.json`. Ultralytics and the
RF-DETR training package are not runtime dependencies. Release builds are rejected
if training-only modules appear in the PyInstaller analysis manifest.

## Data And Evaluation

The current small regression set protects known behavior but is not a release-grade
evaluation. Release requires a source-isolated locked set of at least 750 event
groups, including at least 300 positives and 300 hard negatives, with no adjacent
clips from one physical event split across train and test.

## Known Failure Modes

- Contact can occur between sampled frames.
- Bounding-box or mask overlap can be caused by perspective.
- A parked neighboring vehicle can dominate the image without touching the ego car.
- Vehicle-door articulation is inferred from temporal motion and object proximity;
  the current COCO detector does not have a dedicated vehicle-door class.
- Camera-layout priors may need user correction.
- Timing confidence may be low when motion is gradual or occluded.

## Human Control

Users can change review status and correct the key moment. Mimir keeps its original
result separate from those corrections.
