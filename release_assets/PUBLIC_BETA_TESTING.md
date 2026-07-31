# Mimir Public Beta Testing

## Install

1. Open `README_START_HERE.html`.
2. Run `MimirSetup.exe`.
3. Use only the signed public beta installer from the official download page. Do not install internal or unsigned builds.
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

Open an incident, pick an AI feedback label, add a note if you want, and click **Send feedback**. Mimir saves the feedback file, opens its folder, copies the email text to your clipboard, and tries to open your default mail app (which may not be set up -- most people don't have one). If nothing opens, just paste what's on your clipboard into whatever email you actually use, addressed to feedback.mimir@gmail.com, and attach the file. Nothing is sent automatically; the app never uploads anything on its own.

For each issue, it helps to mention:

- Original clip filename.
- What Mimir classified it as.
- What you expected: Important, Review, or Ignore.
- Whether it involved possible impact, door contact, vandalism, normal traffic, or harmless movement.
- Whether the video played correctly in the viewer.
- Whether the timeline markers made sense.
- Any error message shown by the app.

Do not share private footage over public channels (forums, social media, chat rooms) -- send it directly to feedback.mimir@gmail.com.

## Known Limitations

- Mimir ships with a stock, general-purpose detector, not yet fine-tuned on real Sentry footage. It can miss real events and can flag ordinary activity as noteworthy.
- Public beta installers must be signed. Internal unsigned builds are not for distribution.
- Some media formats may not preview in the viewer even if the scan output is valid.
- Move to Mimir Trash is recoverable, but there is no in-app restore button yet.
- Cloud sync and account features are not part of this beta.
