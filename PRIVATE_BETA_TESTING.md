# Mimir Beta Testing

## How to install

Run the Windows installer from the release build output. For this unsigned beta, Windows may show a warning before opening the installer.

Installer artifacts are created under:

```text
C:\Mimir\src-tauri\target\release\bundle\nsis\
```

## How to select a USB drive or footage folder

1. Open Mimir.
2. Click **Choose USB drive or footage folder**.
3. Select the USB drive root, the footage folder, or any folder containing MP4 clips.
4. Confirm the selected path is shown on the import screen.
5. Pick a scan mode and click **Analyze footage**.

## Scan modes

- **Fast**: Quick scan, fewer AI checks.
- **Balanced**: Recommended. Good speed and detection quality.
- **Quality**: Slower scan with more careful review.

## What happens during scan

Scanning is review-only. Mimir reads the selected folder and writes local results, but it does not move, copy, or delete clips during scan.

Your original clips stay where they are until you manually choose an action after reviewing an incident.

## How to change status

Open an incident from the library, then use the status buttons:

- **Ignore**
- **Review**
- **Important**

The session is updated after the action succeeds, and the library counts refresh from `latest_session.json`.

## Move to Mimir Library

Use **Move to Mimir Library** from the incident viewer when you want to keep a reviewed clip on the PC.

Mimir moves the selected incident clip into the local Mimir Library and updates the incident path in the session output.

Default library location:

```text
%USERPROFILE%\Videos\Mimir Library
```

## Mimir Trash

**Move to Mimir Trash** is not permanent deletion. It moves the selected clip into the local Mimir Trash folder so it can be recovered if needed.

Default trash location:

```text
%USERPROFILE%\Videos\Mimir Library\_Mimir Trash
```

## How to export feedback

Use the app's feedback export feature when available, or send a manual report with:

- Original clip filename.
- What Mimir classified it as.
- What you expected: Important, Review, or Ignore.
- Whether the issue was missed impact, door contact, vandalism, normal traffic, harmless movement, or another case.
- A short note describing what happened.

Email reports to **feedback.mimir@gmail.com**. Do not share private footage over public channels (forums, social media, chat rooms) -- send it directly to that address.

## Known limitations

- Mimir is still learning and may misclassify events.
- Always review important footage yourself before moving clips or sending them to Mimir Trash.
- The beta is local-first and does not include cloud accounts or shared review.
- The installer is unsigned for early beta testing.
- Some media formats may not preview in the viewer even if the scan output is valid.
- Detection thresholds for rear-end crashes, door dings, and close contact events are still being tuned.
