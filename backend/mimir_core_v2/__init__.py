"""Mimir Core v2.

This package is a clean, event-based scanner prototype. It intentionally lives
beside the existing scanner so the current production path stays untouched.
"""

SCHEMA_VERSION = "mimir_v2"
SCANNER_VERSION = "mimir_core_v2_0_5"
CORE_VERSION = "0.5.0-beta.2"
CORE_BUILD_ID = "mimir_core_v2_0_5_door_articulation_guard"
GENERATED_BY = "mimir_core_v2"
SCAN_COMMAND_VERSION = "mimir_core_v2_scan_0_5"
FEATURE_FLAGS = {
    "key_moments": True,
    "thumbnails": True,
    "no_yolo_crash_fallback": True,
    "controlled_ai_review": True,
    "benchmark_source_sets": True,
    "structured_progress": True,
    "structured_progress_v2": True,
    "session_history": True,
    "detector_result_cache": True,
    "metadata_only_analysis_cache": True,
    "sequential_frame_decoding": True,
    "asynchronous_ai_enrichment": True,
    "dense_key_moment_refinement": True,
    "camera_aware_ego_mask": True,
    "two_pass_contact_timing": True,
    "multi_camera_contact_corroboration": True,
    "transactional_storage_actions": True,
    "consent_first_dataset_export": True,
    "free_invite_beta": True,
    "permissive_detector_ready": True,
    "rfdetr_onnx_detector": True,
    "licensed_auxiliary_shadow_training": True,
}
