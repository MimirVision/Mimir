# Mimir Beta Limitations

Tesla Sentry footage is the supported beta format. Generic MP4 files are accepted
on a best-effort basis and may lack reliable camera identity, event grouping, or
timestamps.

Mimir identifies visual and motion evidence that may deserve review. Image-space
overlap, mask intersection, proximity, or a motion impulse can support an
"apparent visual contact" candidate but cannot prove physical contact. Poor light,
rain, reflections, occlusion, lens distortion, camera shake, removed USB drives,
corrupt clips, and unfamiliar camera layouts can reduce accuracy.

Local object detection is only one evidence source. Person presence alone is not
hard-impact evidence. Person/pass-by safety caps remain in force. The no-object-
detection fallback is limited to strong crash-like motion and does not use filenames
as evidence.

Experimental local AI is off by default, is a second opinion only, and cannot
downgrade hard local impact/contact evidence. It may describe scenes incorrectly.

Current external-release blockers are tracked by the strict release checker. A
developer build passing its small regression set does not establish the public-beta
accuracy targets.
