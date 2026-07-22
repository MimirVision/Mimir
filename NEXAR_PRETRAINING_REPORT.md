# Nexar Auxiliary Pretraining Report

Date: 2026-07-22

## Source

- Repository: `nexar-ai/nexar_collision_prediction`
- Revision: `aa97deda5a59f00bb7187739053b7c72e14374df`
- Local source receipt: `C:\Mimir_Data\external_sources\nexar_collision_prediction\MIMIR_SOURCE_RECEIPT.json`
- Purpose: auxiliary collision-timing pretraining only
- Tesla release evaluation eligible: no

The source's positive class combines collisions and near-misses. It is not treated
as verified physical contact, and source filenames or descriptions are not model
features.

## Verified Inventory

- Training partition: 1,500 videos (750 positive, 750 negative)
- Official public test: 667 videos
- Official private lockbox: 677 videos
- Total verified videos: 2,844
- Prepared split: 1,350 train, 150 validation, 667 public test, 677 private lockbox
- Feature extraction: 10 FPS, 2,844 sequences, zero decode failures

The official private lockbox was not scored or used for model selection.

## Public-Test Results

| Run | Architecture | AUROC | Average precision | Recall at 0.5 | FPR at 0.5 |
| --- | --- | ---: | ---: | ---: | ---: |
| `20260722T141825Z-bc6e20cb` | `dilated_conv_v2_receptive_field_6s` | 0.5834 | 0.5745 | 0.0000 | 0.0120 |
| `20260722T142126Z-b4c5985e` | `dilated_conv_v3_alert_window_6s` | 0.5836 | 0.5662 | 0.0120 | 0.0120 |

## Decision

Rejected for beta and rejected as an initializer for the Tesla contact verifier.
Motion-only features did not generalize adequately to the official public test.
The next candidate must add semantic visual tracks and consented parked-Tesla
contact data before the private lockbox is opened or any production promotion is
considered.
