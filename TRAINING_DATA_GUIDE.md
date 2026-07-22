# Mimir Training Data Guide

Mimir training is consent-first. Do not place regression clips, downloaded social
media videos, or footage with uncertain rights in `training_data`.

## Environments

- `.venv-runtime`: scanner, packaging, and release checks only.
- `.venv-training`: dataset preparation and RF-DETR fine-tuning only.

Install training dependencies with:

```powershell
python -m venv .venv-training
.\.venv-training\Scripts\python.exe -m pip install -r requirements-training.txt
```

## Source Policy

Review the source registry before collecting data:

```powershell
.\.venv-training\Scripts\python.exe mimir_core_v2_training.py sources
```

The Nexar collision dataset is the first approved external candidate. Its metadata
and license may be retrieved without footage. Downloading footage requires the
operator to review and explicitly accept its terms:

```powershell
.\.venv-training\Scripts\python.exe mimir_core_v2_training.py fetch-source `
  --source nexar_collision_prediction `
  --output training_data\external_sources\nexar_collision_prediction
```

Add `--include-footage --accept-license` only after that review. Mimir never accepts
dataset terms automatically.

## Export Owned Or Permitted Footage

Export only incident IDs for which training rights were confirmed:

```powershell
.\.venv-training\Scripts\python.exe mimir_core_v2_dataset.py export `
  --session MimirOutputV2\latest_session.json `
  --output training_data\collections\collection_001 `
  --consent-incident incident_0001 `
  --recorded-by "operator name" `
  --rights-confirmed `
  --rights-basis owned `
  --include-video
```

This copies only the selected media, records checksums and provenance, and creates
annotation JSON. It does not upload anything.

## Human Annotation

Each annotation must include:

- `human_severity`: `IGNORE`, `REVIEW`, or `IMPORTANT`
- `contact_outcome`: `contact`, `impact`, `no_contact`, or `uncertain`
- a human contact/impact time for positive clips
- object boxes or masks for `person`, `vehicle`, `vehicle_door`, and `ego_vehicle`
  when those objects are visible
- door state, closest approach, and annotator notes when relevant

Keep every physical event and adjacent clips in one split by using a stable
`--source-group`. Never tune on the locked test split.

List the queue and record temporal labels with the guarded annotation command:

```powershell
.\.venv-training\Scripts\python.exe mimir_core_v2_dataset.py list `
  --dataset training_data\collections\collection_001

.\.venv-training\Scripts\python.exe mimir_core_v2_dataset.py annotate `
  --dataset training_data\collections\collection_001 `
  --incident incident_0001 `
  --annotated-by "annotator name" `
  --human-severity REVIEW `
  --contact-outcome contact `
  --apparent-contact-time-sec 12.4 `
  --door-state opening
```

Frame-level boxes and masks can be supplied with `--objects` using a reviewed JSON
list. The command validates class names, times, and bounding boxes before writing.

## Audit, Prepare, Train

```powershell
.\.venv-training\Scripts\python.exe mimir_core_v2_training.py audit `
  --dataset-root training_data

.\.venv-training\Scripts\python.exe mimir_core_v2_training.py prepare `
  --dataset-root training_data `
  --output training_runs\prepared_v1

.\.venv-training\Scripts\python.exe mimir_core_v2_training.py train `
  --prepared training_runs\prepared_v1 `
  --output training_runs\rfdetr_v1
```

Training refuses to start until rights, annotation completeness, source-isolated
splits, and minimum pilot coverage pass. This is intentional. The six current
regression clips are not a training dataset.

## Release Thresholds

The pilot training minimum is only a pipeline guard. A release evaluation still
requires at least 2,500 event groups and a locked 750-group test set with the recall,
false-Important, and key-moment timing gates in the release plan.
