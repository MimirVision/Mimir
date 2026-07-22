# Mimir Training Data Guide

Mimir training is consent-first. Do not place regression clips, downloaded social
media videos, or footage with uncertain rights in `training_data`.

## Environments

- `.venv-runtime`: scanner, packaging, and release checks only.
- `.venv-training`: dataset preparation and candidate training only.

```powershell
python -m venv .venv-training
.\.venv-training\Scripts\python.exe -m pip install -r requirements-training.txt
```

## Source Policy

```powershell
.\.venv-training\Scripts\python.exe mimir_core_v2_training.py sources
```

The Nexar dataset is an eligible external candidate only after the operator reviews
and explicitly accepts its terms. Metadata and license files may be retrieved first:

```powershell
.\.venv-training\Scripts\python.exe mimir_core_v2_training.py fetch-source `
  --source nexar_collision_prediction `
  --output training_data\external_sources\nexar_collision_prediction
```

Add `--include-footage --accept-license` only after that review. Mimir never accepts
dataset terms automatically.

## Encrypted Contribution Export

The tester-facing path produces a manually transferable encrypted package:

```powershell
python mimir_core_v2_dataset.py export-encrypted `
  --session MimirOutputV2\latest_session.json `
  --output C:\Exports\incident_0001.mimir-dataset.age `
  --consent-incident incident_0001 `
  --recorded-by "operator name" `
  --rights-confirmed `
  --rights-basis owned `
  --permission-reference "Recorded by me on my vehicle" `
  --recipient-file C:\Mimir_Data\keys\mimir-training-recipient.txt
```

Only selected incident media is copied into a temporary collection. The command
records clip-by-clip consent, hashes, provenance, and a complete inventory, encrypts
the package with `age`, then removes the temporary plaintext package. Nothing is
uploaded. Social-media regression clips are excluded by hash unless an independent
permission record is bundled.

## Idempotent Intake And CVAT

```powershell
python mimir_core_v2_dataset.py intake `
  --package C:\Incoming\incident_0001.mimir-dataset.age `
  --identity C:\Mimir_Data\keys\mimir-training-intake-identity.txt `
  --dataset-root C:\Mimir_Data\training `
  --create-cvat-tasks
```

Intake validates encryption, consent, every file hash, duplicate footage, global
source splits, and exclusions before copying the collection. Re-running the same
package is safe and reports `already_imported`. If CVAT is temporarily unavailable,
the accepted collection is retained as `cvat_status: pending`; retrying intake reuses
any already-created task names rather than duplicating them.

CVAT Community is pinned in `mimir_core_v2/cvat_deployment.json`:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_cvat.ps1
python mimir_core_v2_cvat.py health
python mimir_core_v2_cvat.py ensure-project
```

Credentials and API tokens stay under `C:\Mimir_Data\cvat\credentials`, never Git.
After annotation, preserve the raw export and normalize tracks/masks/timing:

```powershell
python mimir_core_v2_cvat.py sync-annotations `
  --task-id 1 `
  --dataset-root C:\Mimir_Data\training
```

## Annotation Rules

Each complete item needs:

- `human_severity`: `IGNORE`, `REVIEW`, or `IMPORTANT`;
- `contact_outcome`: `contact`, `impact`, `no_contact`, or `uncertain`;
- a human-confirmed contact or impact frame for positives;
- masks/tracks for visible `ego_vehicle`, `person`, `vehicle`, and `vehicle_door`;
- door state, camera, and closest approach when visible.

Pixel intersection is apparent visual contact, not proof of physical contact. Prefer
`uncertain` over guessing. CVAT timing is imported only when `timing_confirmed` is
checked. Keep adjacent clips from one physical event in the same source group.

At least 10% of the pilot must be blindly re-labeled after a delay:

```powershell
python mimir_core_v2_dataset.py blind-relabel `
  --dataset C:\Mimir_Data\training\collections\PACKAGE_ID `
  --incident incident_0001 `
  --annotated-by "annotator name" `
  --human-severity REVIEW `
  --contact-outcome contact `
  --apparent-contact-time-sec 12.4
```

## Audit, Prepare, And Train Candidates

```powershell
.\.venv-training\Scripts\python.exe mimir_core_v2_training.py audit `
  --dataset-root C:\Mimir_Data\training

.\.venv-training\Scripts\python.exe mimir_core_v2_training.py prepare `
  --dataset-root C:\Mimir_Data\training `
  --output training_runs\prepared_v1

.\.venv-training\Scripts\python.exe mimir_core_v2_training.py train `
  --prepared training_runs\prepared_v1 `
  --output training_runs\rfdetr_candidates `
  --model segmentation

.\.venv-training\Scripts\python.exe mimir_core_v2_training.py infer-perception `
  --prepared training_runs\prepared_v1 `
  --model-manifest training_runs\rfdetr_candidates\RUN\candidate_model_manifest.json `
  --output training_runs\candidate_perception_v1 `
  --sample-fps 15

.\.venv-training\Scripts\python.exe mimir_core_v2_training.py extract-temporal `
  --prepared training_runs\candidate_perception_v1 `
  --output training_runs\temporal_features_v1 `
  --sample-fps 15

.\.venv-training\Scripts\python.exe mimir_core_v2_training.py train-temporal `
  --prepared training_runs\candidate_perception_v1 `
  --features training_runs\temporal_features_v1 `
  --output training_runs\temporal_candidates
```

Training refuses to start before 100 complete groups, 25 positives, 25 hard
negatives, 10% blind re-label coverage, and non-empty source-isolated train,
validation, and locked-test splits. Candidate manifests always have `promoted:
false`. Temporal training selects its checkpoint by validation loss and never trains
on the test split.

## Locked Evaluation

Only `mimir_core_v2_evaluate.py` produces promotion evidence. External beta still
requires 2,500 groups and a locked, source-isolated 750-group test set containing at
least 300 positives and 300 hard negatives. It compares every candidate against the
frozen baseline, writes per-category confusion matrices and timing distributions,
and blocks source leakage or any missed release gate.

Before evaluation, run the RF-DETR segmentation candidate over the locked footage,
store those detections as `perception_objects`, re-extract temporal features, and
produce predictions using the policy frozen in the candidate manifest:

```powershell
python mimir_core_v2_training.py predict-temporal `
  --features training_runs\locked_candidate_features `
  --model-manifest training_runs\temporal_candidates\RUN\candidate_model_manifest.json `
  --split test `
  --output release_assets\candidate_predictions.json
```

The predictor refuses locked evaluation when geometry came from human annotations;
that would leak the answer into the model input. `--training-diagnostics-only` is
available for development checks, but marks its predictions as release-ineligible.
