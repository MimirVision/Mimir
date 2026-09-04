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

Scanning reads the selected folder and builds a local incident timeline. If the footage is on a different drive from your Mimir library, each incident is copied into the library first and scanned from there -- reading video straight off a USB stick is slow and, on some machines, has crashed Windows mid-scan. Your originals are never edited, and nothing is deleted unless you tick **Clear the drive as it goes**, which is off by default and only removes an incident once its copy has been verified byte for byte.

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

## How to Tell Mimir It Was Wrong

Open an incident and change the verdict -- `I` for Important, `R` for Review,
`G` for Ignore, or click the buttons. That correction is the feedback. Mimir
asks permission the first time and then sends them quietly; you can stop at any
point under **Labs -> Sending your corrections**, which also shows how many you
have sent.

A correction carries what Mimir decided, what you decided, and the evidence
behind it. It contains no video. If a clip is worth sending too, tick **Send the
clip too** on that incident before changing the verdict.

Only disagreements are sent. Agreeing still updates the verdict on your machine.

There is a notes field if something needs explaining, and it is worth using for:

- What you expected: Important, Review, or Ignore.
- Whether it involved possible impact, door contact, vandalism, normal traffic,
  or harmless movement.
- Whether the video played correctly in the viewer.
- Whether the timeline markers made sense.
- Any error message shown by the app.

If Mimir crashes, it records what happened and offers to send it under **Labs**.
That report shows you exactly what it contains before anything leaves, and it is
the only way anyone finds out the crash happened.

If you'd rather not send footage over the network for a particular clip, Mimir also offers **Save without sending** wherever Send/Contribute appears -- it still encrypts locally, just doesn't attempt delivery, and you can send it later. For anything you don't want going through Mimir's submission flow at all, reach support@mimirvision.com directly -- but do not share footage over public channels (forums, social media, chat rooms).

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
