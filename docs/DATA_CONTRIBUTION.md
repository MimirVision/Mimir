# Contributing Footage During The Free Beta

Mimir never uploads footage automatically. A tester can choose **Export for Mimir
training** on one incident, confirm their rights for that incident, and save an
encrypted `.mimir-dataset.age` package. The tester then decides whether and how to
transfer that file.

The package includes only the selected incident and its grouped camera angles. It
contains a clip-by-clip consent receipt, source hashes, provenance, annotations,
and a complete package inventory. The intake private key is not included in Mimir.

Do not contribute footage unless you recorded and own it, have explicit permission,
or a public license permits model-development use. A URL or written permission
reference is required. Attach a permission record when ownership is not obvious.

Regression footage from social-media sources is excluded by hash unless independent
training permission is documented. Filenames and user severity labels are never
model inputs.

Contribution is voluntary and is not required to use the free beta. There is no
background collection, account, activation service, subscription, or payment.

## What Happens To A Contribution

Contributions are not a black hole, and they are also not fed straight into a
model. Once a package is received:

1. `mimir_core_v2_pipeline.py process` takes it in automatically -- decrypt,
   validate consent and rights, deduplicate, assign a source-isolated split, and
   optionally queue annotation tasks in the local CVAT instance.
2. Annotators label the real evidence (ego vehicle, person, vehicle, door state,
   closest approach, apparent contact frame, impact frame). Ten percent is blindly
   re-labeled later to measure annotator agreement.
3. Progress toward the pilot gate is tracked and visible at any time:

   ```powershell
   python mimir_core_v2_pipeline.py status --dataset-root <dataset-root>
   ```

   The pilot gate is 100 complete event groups, 25 positives, and 25 hard
   negatives. External beta evaluation requires substantially more (2,500 groups
   and a locked 750-group test set).
4. Once the gate is met, a candidate detector can be fine-tuned and evaluated
   against a locked test set it never trained on.

**The model never updates itself.** No contribution changes detection behavior
automatically, on anyone's machine. A candidate only ships after a human reviews
its evaluation results and explicitly promotes it, and it then reaches users
through the normal model-update mechanism. This is deliberate: a single
mislabeled or malicious clip must never be able to degrade detection for
everyone with no way back.
