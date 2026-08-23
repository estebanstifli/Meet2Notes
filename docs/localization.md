# UI localization

The UI catalogues live in `src/local_meeting_ai/web/static/locales/`.

- `en.json` is the source catalogue.
- Every additional language uses the same keys as `en.json`.
- `index.json` lists languages shown in the interface selector. Add a catalogue
  and its native language name there to enable a new language.
- The application falls back to English when a catalogue cannot be loaded.

Use named keys through `Meet2Notes.t("area.label")` for messages with values
or plural forms. Static UI text and accessible labels are translated by the
shared UI layer, including content inserted by a screen after it has loaded.

Before submitting a UI change, run:

```powershell
.venv\Scripts\python.exe scripts\check_ui_i18n.py
```

It checks catalogue parity, JavaScript key usage, and that visible template
text is catalogued. Product names, model names, file formats, URLs, and other
technical values intentionally remain untranslated.

`RAG`, `Webhook`, `Webhooks`, `Plugin`, `Plugins`, `Prompt`, `Faster Whisper`,
`Word`, and `Markdown` are immutable UI terms. They must remain in English in every
catalogue.
