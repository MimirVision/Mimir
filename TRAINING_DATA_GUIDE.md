# Mimir Training Data Guide

Mimir training is consent-first. Do not place regression clips, downloaded social
media videos, or footage with uncertain rights in `training_data`.

## Environments

- `.venv-runtime`: scanner, packaging, and release checks only.
- `.venv-training`: dataset preparation and candidate training only.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_training_env.ps1
```

The setup script installs pinned CUDA-enabled PyTorch wheels from PyTorch's official
wheel index, then verifies CUDA and ONNX before any training command is allowed.

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

The footage command also requires `--accepted-by` and should include an auditable
`--acceptance-basis`, for example an explicit project-owner instruction.
Use `--include-evaluation-footage` to retrieve Nexar's official public/private
evaluation clips. Development may inspect the public split; the private split must
remain a one-time auxiliary lockbox and is never a Tesla promotion set.

### Nexar Auxiliary Timing Pretraining

Nexar positives combine collisions and near-misses. They must never be imported as
verified physical contact or used as Tesla release-evaluation labels. After licensed
footage retrieval, build a content-hashed, class-balanced auxiliary manifest:

```powershell
python mimir_core_v2_training.py prepare-nexar `
  --source-root C:\Mimir_Data\external_sources\nexar_collision_prediction `
  --output C:\Mimir_Data\prepared\nexar_collision_prediction_v1 `
  --workers 4

python mimir_core_v2_training.py extract-temporal `
  --prepared C:\Mimir_Data\prepared\nexar_collision_prediction_v1 `
  --output C:\Mimir_Data\features\nexar_collision_prediction_v1_10fps `
  --sample-fps 10 `
  --workers 6

python mimir_core_v2_training.py pretrain-temporal `
  --prepared C:\Mimir_Data\prepared\nexar_collision_prediction_v1 `
  --features C:\Mimir_Data\features\nexar_collision_prediction_v1_10fps `
  --output C:\Mimir_Data\training_runs\nexar_event_pretraining
```

The resulting checkpoint is encoder initialization only. It is marked
`promotion_eligible: false`, retains Nexar attribution and license conditions, and
cannot satisfy the consented Tesla pilot or locked evaluation gates.

The prepared source uses 1,350 training clips and 150 validation clips from
Nexar's training partition. The official public test is development evidence only.
The official private test is recorded as `test_private`, is never read by the
pretraining command, and must remain sealed until a materially stronger semantic
candidate has been frozen. Motion-only experiments that fail public evaluation are
retained as negative results rather than being tuned against the private lockbox.

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

## Licensed MEVA Auxiliary Pretraining

MEVA is kept outside the consented target dataset and is never contact truth. A
curated pilot may be resumed, checksummed, and source-isolated with:

```powershell
python mimir_stationary_data.py download-meva `
  --manifest C:\Mimir_Data\external_sources\meva_kf1\stationary_auxiliary_manifest.json `
  --output C:\Mimir_Data\external_sources\meva_kf1\selected_video `
  --max-gib 15 --workers 4 --retries 5 --reserve-gib 25

python mimir_stationary_data.py repair-splits `
  --manifest C:\Mimir_Data\external_sources\meva_kf1\stationary_auxiliary_manifest.json

python mimir_core_v2_train_auxiliary.py `
  --manifest C:\Mimir_Data\external_sources\meva_kf1\stationary_auxiliary_manifest.json `
  --output C:\Mimir_Data\training_runs\meva_auxiliary_shadow
```

The resulting model is explicitly non-promotable. It can measure whether
licensed person/vehicle tracks contain useful stationary door-activity context,
but it cannot classify physical contact and is never loaded by the production
scanner.

## Licensed OTW Door-Activity Pretraining

[Out the Window](https://stresearch.github.io/otw/) is CC BY 4.0 fixed-camera
footage with opening/closing side doors, trunks, loading, people, bicycles, and
other hard negatives. It is closer to parked activity than a moving dashcam, but
it still has no physical-contact truth. Mimir therefore trains it only as a
three-way shadow model: side-door activity, other vehicle access, and unrelated
activity.

```powershell
python mimir_otw_data.py catalog `
  --metadata-repo C:\Mimir_Data\external_sources\otw\metadata_repo `
  --output C:\Mimir_Data\external_sources\otw\otw_auxiliary_manifest.json `
  --max-videos 320

python mimir_otw_data.py extract `
  --manifest C:\Mimir_Data\external_sources\otw\otw_auxiliary_manifest.json `
  --archive C:\Mimir_Data\external_sources\otw\otw.tar.gz `
  --output C:\Mimir_Data\external_sources\otw\selected_video

python mimir_core_v2_train_otw.py build-cache `
  --manifest C:\Mimir_Data\external_sources\otw\otw_auxiliary_manifest.json `
  --output C:\Mimir_Data\features\otw_temporal_crops_v1.npz

python mimir_core_v2_train_otw.py train `
  --manifest C:\Mimir_Data\external_sources\otw\otw_auxiliary_manifest.json `
  --cache C:\Mimir_Data\features\otw_temporal_crops_v1.npz `
  --output C:\Mimir_Data\training_runs\otw_door_articulation_shadow
```

The archive must match the publisher's MD5
`9096bad6ff78056b505fafb4cded1734`. Extraction is restricted to the
source-isolated selected-video manifest. The output ONNX and checkpoint remain
`promotion_eligible: false` and `physical_contact_claim_allowed: false`.

## CARLA Synthetic Collision-Timing Pretraining

CARLA 0.9.16 provides exact simulator collision frames, actor identity, and
normal impulse. CARLA code is MIT licensed and CARLA-specific assets are CC BY.
Generated sequences remain synthetic auxiliary data: they cannot establish
real-world contact accuracy or unlock production promotion.

```powershell
python mimir_carla_data.py download `
  --output C:\Mimir_Data\external_sources\carla

python mimir_carla_data.py verify `
  --root C:\Mimir_Data\external_sources\carla `
  --report C:\Mimir_Data\external_sources\carla\integrity_report.json

python mimir_carla_data.py extract `
  --root C:\Mimir_Data\external_sources\carla `
  --output C:\Mimir_Data\external_sources\carla\CARLA_0.9.16

python mimir_carla_batch.py `
  --carla-root C:\Mimir_Data\external_sources\carla\CARLA_0.9.16 `
  --output C:\Mimir_Data\prepared\carla_stationary_collision_v2 `
  --total-scenarios 60 --chunk-size 3

python mimir_carla_data.py verify-prepared `
  --root C:\Mimir_Data\prepared\carla_stationary_collision_v2 `
  --expected-scenarios 60 `
  --report C:\Mimir_Data\prepared\carla_stationary_collision_v2\integrity_report.json

python mimir_core_v2_train_carla.py extract `
  --prepared C:\Mimir_Data\prepared\carla_stationary_collision_v2 `
  --output C:\Mimir_Data\features\carla_stationary_collision_v2 `
  --sample-fps 10 --workers 4

python mimir_core_v2_train_carla.py train `
  --prepared C:\Mimir_Data\prepared\carla_stationary_collision_v2 `
  --features C:\Mimir_Data\features\carla_stationary_collision_v2 `
  --output C:\Mimir_Data\training_runs\carla_collision_timing_shadow
```

The generated manifest labels an impact only when CARLA's collision sensor
records it. Intended scenarios that fail to collide are retained by their actual
sensor outcome. The prepared-data verifier checks video and mask hashes,
readability, collision-time consistency, and class coverage in every split.
A synthetic held-out split measures simulator learning only;
consented Tesla fine-tuning and locked Tesla evaluation remain mandatory.

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
  --sample-fps 15 `
  --workers 6

.\.venv-training\Scripts\python.exe mimir_core_v2_training.py train-temporal `
  --prepared training_runs\candidate_perception_v1 `
  --features training_runs\temporal_features_v1 `
  --output training_runs\temporal_candidates `
  --pretrained-checkpoint C:\Mimir_Data\training_runs\nexar_event_pretraining\RUN\nexar_event_timing_pretrainer.pt
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
