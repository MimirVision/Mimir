# Third-Party Notices

## RF-DETR Small

Mimir Core v2 uses an ONNX export of RF-DETR Small for local person and
vehicle candidate detection.

- Project: RF-DETR
- Copyright: 2025 Roboflow
- Source: https://github.com/roboflow/rf-detr
- Package version used for export: 1.8.3
- License designation: Apache License 2.0
- License text: `licenses/RF-DETR-APACHE-2.0.txt`

The detector supplies supporting object and proximity evidence only. Mimir's
temporal local analysis is responsible for contact/impact candidates, and a
bounding box overlap is not presented as proof of physical contact.
