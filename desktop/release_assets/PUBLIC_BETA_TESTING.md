# Mimir Public Beta Testing

## Install

1. Open `README_START_HERE.html`.
2. Run `MimirSetup.exe`.
3. Windows will warn that the app is unrecognized -- it is not code-signed yet. Choose the advanced option to continue.
4. Open Mimir from the Start menu or desktop shortcut if one was created.

## How to select a USB drive or footage folder

1. Open Mimir.
2. Click **Choose USB drive or footage folder**.
3. Select the USB drive root, the footage folder, or any folder containing MP4 clips.
4. Confirm the selected path is shown on the import screen.
5. Pick a scan mode and click **Analyze footage**.

Scanning reads the selected folder and builds a local incident timeline. It does not move, copy, or delete clips during scan; your original clips stay where they are until you manually choose an action after reviewing an incident.

## Scan Modes

- **Fast**: Quick scan, fewer AI checks. Use this for a first pass on larger folders.
- **Balanced**: Recommended. Good speed and detection quality for most testers.
- **Thorough**: Slower scan with more careful review. Use this when a folder contains subtle or important events.

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
%USERPROFILE%\Videos\Mimir Library
```

## Mimir Trash

Use **Move to Mimir Trash** for clips you no longer want in normal review. This does not permanently delete the file. The clip is moved to:

```text
%USERPROFILE%\Videos\Mimir Library\_Mimir Trash
```

Deleted/trash incidents are hidden from normal Important, Review, and Ignored lists by default, and can be recovered from Mimir Trash.

## How to Send Feedback

Open an incident, pick an AI feedback label, add a note if you want, and click **Send feedback**. Mimir saves the feedback locally, then encrypts it on your device and sends it in that same action -- nothing is sent until you click that button, and Mimir's developer is the only one who can decrypt what arrives. There's no email step anymore: no clipboard copying, no mail app to configure.

For each issue, it helps to mention in the notes field:

- Original clip filename.
- What Mimir classified it as.
- What you expected: Important, Review, or Ignore.
- Whether it involved possible impact, door contact, vandalism, normal traffic, or harmless movement.
- Whether the video played correctly in the viewer.
- Whether the timeline markers made sense.
- Any error message shown by the app.

If you'd rather not send footage over the network for a particular clip, Mimir also offers **Save without sending** wherever Send/Contribute appears -- it still encrypts locally, just doesn't attempt delivery, and you can send it later. For anything you don't want going through Mimir's submission flow at all, reach feedback.mimir@gmail.com directly -- but do not share footage over public channels (forums, social media, chat rooms).

## Known Limitations

**See [KNOWN_ISSUES.md](KNOWN_ISSUES.md) for the full, measured version** -- how
often Mimir over-flags, how long a scan actually takes on GPU versus CPU, and
what it deliberately will not do. Worth reading before you start, so you know
what you are looking at.

- Mimir ships with a stock, general-purpose detector, not yet fine-tuned on real Sentry footage. It can miss real events and can flag ordinary activity as noteworthy. In beta feedback so far, most clips rated IMPORTANT were rated lower by the person who reviewed them -- treat IMPORTANT as "look at this", not "something happened".
- The installer is not code-signed, so Windows SmartScreen warns on first run.
- Some media formats may not preview in the viewer even if the scan output is valid.
- Move to Mimir Trash is recoverable, but there is no in-app restore button yet.
- Cloud sync and account features are not part of this beta.
