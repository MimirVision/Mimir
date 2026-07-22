# Annotation And Model Promotion

Mimir uses a pinned, self-hosted CVAT Community deployment. The annotation project
tracks `ego_vehicle`, `person`, `vehicle`, `vehicle_door`, event outcome, human
severity, door state, closest approach, apparent contact frame, and impact frame.

Annotators should use `uncertain` rather than guess. Pixel overlap is recorded as
apparent visual contact, not proof of physical contact. Positive examples require a
human-confirmed contact or impact frame. Ten percent of the pilot is blindly
re-labeled after a delay and intra-annotator agreement is recorded.

The pilot gate is 100 complete event groups, 25 positives, and 25 hard negatives.
No candidate can be promoted from the pilot alone. External beta evaluation requires
2,500 total groups and a locked, source-isolated 750-group test set with at least 300
positives and 300 hard negatives.

RF-DETR segmentation and the temporal contact verifier are trained independently.
Candidate manifests remain explicitly unpromoted until the locked evaluator confirms
all recall, false-Important, false-Ignore, timing, source-leakage, and baseline
non-regression gates.
