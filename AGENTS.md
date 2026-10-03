# Bluey development

- Keep the original `Mac/`, `iOS/`, and `Shared/` Apple applications compatible.
- Windows code lives in `Windows/bluey` (Python 3.12, PySide6). Android is a native Kotlin app under `Android/` (API 26+, Java 17).
- New clients use bounded newline JSON over TLS. Pin the certificate SHA-256 from the pairing code before sending the pairing key. Authenticate before accepting commands. Never weaken these checks.
- OpenAI's long-lived API key stays on Windows in Credential Manager. Android receives only ephemeral client secrets. Never log pairing codes, certificates' private keys, audio, screenshots, or API tokens.
- Computer control starts disabled. Each action needs local approval, validated arguments, a recent screenshot, and a still-active session. Stop/disconnect invalidate pending actions.
- Keep prompts focused on one milestone. Read only relevant files; avoid speculative refactors and new dependencies.
- Run `cd Windows && python -m unittest discover -s tests -v`. Compile Android with Java 17 and Gradle 8.9: `cd Android && gradle assembleDebug`. Use CI for Windows packaging and Android SDK builds.
- Do not claim an OS/device was tested unless it actually was. List unsupported features and unverified hardware flows.
- No license file exists upstream at the initial port commit. Do not add an assumed license or claim redistribution rights. Obtain upstream permission before distributing builds outside a private evaluation.
