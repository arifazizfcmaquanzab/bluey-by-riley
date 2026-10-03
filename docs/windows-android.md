# Windows PC + Android phone, preview port

This branch adds a native Windows host (Python/Qt) and native Android companion (Kotlin). The original Mac/iPhone source is preserved. The new clients form a separate pair: Android does not currently connect to the Mac app, and iPhone does not currently connect to the Windows host.

## Get the applications

Open this fork's **Actions → Windows and Android**. For a successful run on `feat/windows-android`, download both artifacts:

- **Bluey-Windows**: extract the entire ZIP, then launch `Bluey.exe` from the extracted folder. Keep all its accompanying files. This is a portable app, not an installer, and the build is unsigned.
- **Bluey-Android-debug**: extract `app-debug.apk`, copy it to the phone, and allow that file's installer to install unknown apps. This is a debug APK for private evaluation, not a Play Store release. The CI debug signing key changes across fresh runners, so a later build may require uninstalling the earlier debug app first.

GitHub requires signing in to download workflow artifacts. Fork workflows may need to be enabled in Actions first. A green build establishes compilation and packaging, not hardware compatibility.

### Run Windows from source

Install Python **3.12** for Windows with the Python launcher. Download this branch or clone it. In PowerShell from the repository:

```powershell
powershell -ExecutionPolicy Bypass -File .\Windows\run.ps1
```

The launcher creates a virtual environment and installs dependencies. To launch again without reinstalling:

```powershell
cd Windows
.\.venv\Scripts\python.exe -m bluey
```

Use Windows 10/11 and Android 8 or newer. These are targets, not a claim of completed device verification.

### Build Android yourself

Open `Android/` in Android Studio, let it install SDK 35, and use Java 17 and **Gradle 8.9**. If prompted for the Gradle distribution, select that version. There is no checked-in Gradle wrapper binary. With a matching `gradle` on PATH:

```sh
cd Android
gradle assembleDebug lintDebug
```

The APK is `Android/app/build/outputs/apk/debug/app-debug.apk`.

## Pair and use

1. Put PC and phone on the same **private** Wi-Fi. Guest Wi-Fi/client isolation prevents pairing.
2. Start Bluey on Windows. Select the PC's Wi-Fi IPv4 address, not a VPN or virtual adapter. Refresh addresses if needed.
3. If Windows Firewall prompts, allow the host only on **private networks**. Pairing uses TCP **18466**. If blocked without a prompt, add an inbound rule for the app/Python on this port and private profile, scoped to the local subnet. Do not open this port on your router.
4. Enter an OpenAI API key on Windows and save it. It uses Windows Credential Manager. The default Realtime model follows upstream (`gpt-realtime-2.1`); change it to a Realtime model your API project can access if needed. ChatGPT Plus is not API credit.
5. Click **Copy pairing code**, transfer it privately to the phone, paste it, and tap **Connect to PC**. The code contains a random pairing key and certificate fingerprint; do not post it publicly. Each PC launch generates a new pairing key, so copy its current code after relaunching.
6. Tap **Wake**, allow microphone access, and wait for **Listening**. If the Android permission dialog paused the app, reconnect before waking again.
7. While awake, the microphone continuously streams to OpenAI. Bluey stays quiet until you **hold to ask**, speak, then release. Responses appear on the PC and phone. You can also type a question after waking. This initial port commits the entire uncommitted audio context when you release; it does not yet implement upstream's per-turn ambient transcription.
8. Ask "What is under my mouse?" or "Look at my screen and point at the chart." Minimize the host settings window first. Screen images are sent to OpenAI only when the screen tool is called.
9. **Sleep**, backgrounding the Android app, disconnecting, or **Ctrl+Alt+S** on Windows stops voice. Keep the Android app in the foreground; background operation is deliberately unsupported. When you return, reconnect.

Computer control starts **off**. Enable the Windows checkbox before waking to advertise its tools. Every proposed action requires a Windows confirmation. To refresh the advertised tools after changing the checkbox, sleep and wake again. Confirm only if the target window and field are correct. The host cannot automatically identify password fields; the model is instructed to avoid them, and local approval is the enforcement boundary. Move the real mouse to the top-left corner to trigger PyAutoGUI's failsafe during actions.

## Current feature scope

| Feature | Windows + Android preview |
| --- | --- |
| Animated eyes following PC pointer | Included |
| Voice input, text replies | Included, OpenAI API key required |
| Encrypted local pairing | TLS, certificate pin, random pairing key, one phone |
| Screen viewing and coordinate pointing | Primary display only |
| Click, drag, vertical scroll | Local approval for each action |
| Keyboard shortcuts | Small allowlist, no logout/lock/force-quit shortcuts |
| Text entry | Printable ASCII only, 2,000 characters maximum |
| Open apps | Notepad, Calculator, Paint only |
| Open websites | HTTP/HTTPS links, local approval |
| Bonjour discovery, QR scan | Not yet included, paste pairing code |
| OCR/accessibility element IDs | Not included, screenshot coordinates replace IDs |
| Web research/report cards | Not included |
| Chirp audio, transcript history | Not included |
| Original animations and pointer choreography | Simplified native rendering |
| Mixed Apple/Windows/Android pairing | Not included |
| Multi-monitor, background microphone, Play Store release | Not included |

## Verification

Run from `Windows/`:

```sh
python -m unittest discover -s tests -v
```

Protocol tests exercise real local TLS connections, authentication rejection, framing bounds, coordinate validation, disabled action tool exposure, and action argument restrictions. GUI guard tests use Qt's offscreen mode and mocked actions. CI builds and smoke-launches the Windows portable bundle and runs Android assembly/lint. The initial 17 Python tests passed locally and on a Windows CI runner; Android debug assembly and lint also passed locally. Physical-device verification is still outstanding.

Before calling this ready for daily use, test on a physical PC and phone:

- Pair using the current code; reject a changed fingerprint/key; disconnect cleanly.
- Wake after allowing the microphone, hold to ask, receive a text answer, and sleep.
- Verify the Android microphone indicator goes away after Sleep, backgrounding, and PC Stop.
- Look/point at all four corners of the primary display, including at 125% and 150% display scaling.
- Keep controls disabled and verify action rejection. Enable them, approve a Notepad click/type, then refuse a second action.
- Press Ctrl+Alt+S while an approval is open, and verify it cannot execute afterwards.
- Reject a stale screenshot, blocked shortcut, invalid coordinate, or connection lost during approval.
- Confirm credentials do not appear in logs or source, and restart Windows to verify saved key retrieval.

The upstream repository has no license file at the base commit. Its authorship is retained. Clarify reuse/distribution permission with the author before commercial use or publishing binaries.
