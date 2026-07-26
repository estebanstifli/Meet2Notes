# Privacy and security

- The HTTP server binds to `127.0.0.1` unless the operator explicitly changes it.
- No telemetry or third-party network service is enabled.
- Imported media is streamed into an internally generated path; client filenames
  are metadata only and never become storage paths.
- Extensions, upload limits, and decoded media structure are validated.
- Original files are retained unchanged.
- A meeting deletion removes its database records and private storage directory.
- Recording begins only after the user confirms a source in the start dialog.
- Native audio is written directly to private local meeting storage; it is never
  routed through a browser service or uploaded.
- Real-time inference uses short temporary WAV windows inside the same private
  meeting directory. Each window is removed immediately after local inference.
- Only one capture session can be active, and the UI always exposes visible pause
  and stop controls while it is running.
- Remote AI providers will require a visible per-request confirmation before any
  transcript content can leave the computer.
- Model installers connect only to the official model hosts when the user runs
  the installer, invokes `meet2notes-models`, or confirms installation in
  Settings. Meeting content is never part of those requests.

Anyone recording a conversation is responsible for obtaining the consent required
by the laws and policies that apply to them.
