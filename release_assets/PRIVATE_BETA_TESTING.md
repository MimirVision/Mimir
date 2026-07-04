# Mimir Private Beta Testing

## Install

1. Open `README_START_HERE.html`.
2. Run `MimirSetup.exe`.
3. If Windows shows an unsigned app warning, choose the advanced option to continue.
4. Open Mimir from the Start menu or desktop shortcut if one was created.

## Scan Test Footage

To test with the current local test folder:

```text
C:\mimir\test
```

You can also choose another folder containing vehicle or security footage. In the beta flow, scanning reads the selected folder and builds a local incident timeline.

## Scan Modes

- **Fast**: Quick scan, fewer AI checks. Use this for a first pass on larger folders.
- **Balanced**: Recommended. Good speed and detection quality for most testers.
- **Quality**: Slower scan with more careful review. Use this when a folder contains subtle or important events.

## Manual Status Changes

Open an incident and use the review controls:

- **Ignore**: The moment is not worth keeping in the active review list.
- **Review**: The moment may matter, but is uncertain.
- **Important**: The moment is likely important and should be kept for review.

Changing status updates the local `latest_session.json` file and refreshes the library counts.

## Move to Mimir Library

Use **Move to Mimir Library** only after reviewing an incident. This moves the clip into the local Mimir Library on the PC and updates the incident video path.

Default location:

```text
%USERPROFILE%\Videos\Mimir Library\Manual Imports
```

## Mimir Trash

Use **Move to Mimir Trash** for clips you no longer want in normal review. This does not permanently delete the file. The clip is moved to:

```text
%USERPROFILE%\Videos\Mimir Library\_Mimir Trash
```

Deleted/trash incidents are hidden from normal Important, Review, and Ignored lists by default.

## What Feedback We Need

For each issue, please report:

- Original clip filename.
- What Mimir classified it as.
- What you expected: Important, Review, or Ignore.
- Whether it involved possible impact, door contact, vandalism, normal traffic, or harmless movement.
- Whether the video played correctly in the viewer.
- Whether the timeline markers made sense.
- Any error message shown by the app.

Do not post private footage publicly. Use the private tester transfer method agreed with the Mimir team.

## Known Limitations

- The installer is unsigned during private beta, so Windows may show a warning.
- The backend must be available at the expected local path for this development beta build.
- Some AI summaries may be conservative or imperfect.
- Move to Mimir Trash is recoverable, but there is no in-app restore button yet.
- Cloud sync and account features are not part of this beta.
