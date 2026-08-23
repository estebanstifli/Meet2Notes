"""Fail when UI catalogs diverge or a referenced translation key is missing."""

from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "src" / "local_meeting_ai" / "web" / "static" / "locales"
SCRIPTS = ROOT / "src" / "local_meeting_ai" / "web" / "static" / "js"
TEMPLATES = ROOT / "src" / "local_meeting_ai" / "web" / "templates"
KEY_USE = re.compile(r'(?:Meet2Notes\.)?\bt\("([^"]+)"')
TECHNICAL_LITERALS = {
    ".venv", "0%", "00:00", "404", "English", "Meet2Notes", "×", "—", "✓",
    "http://127.0.0.1:11434", "http://127.0.0.1:8080/v1",
    "https://automation.example.com/meet2notes",
}
IMMUTABLE_UI_TERMS = {
    "RAG", "Webhook", "Webhooks", "Plugin", "Plugins", "Prompt", "Faster Whisper", "Word", "Markdown",
}


def load_catalogs() -> dict[str, dict[str, object]]:
    catalogs = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in LOCALES.glob("*.json")
        if path.stem != "index"
    }
    if "en" not in catalogs:
        raise RuntimeError("The English source catalog is required")
    return catalogs


class VisibleTemplateLiterals(HTMLParser):
    ignored = {"script", "style", "svg", "path"}
    attributes = {"title", "aria-label", "placeholder"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.values: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack.append(tag)
        if not any(item in self.ignored for item in self.stack):
            self.values.update(value.strip() for name, value in attrs if name in self.attributes and value and "{{" not in value)

    def handle_endtag(self, tag: str) -> None:
        if self.stack:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value and "{{" not in value and "{%" not in value and not any(item in self.ignored for item in self.stack):
            self.values.add(value)


def main() -> int:
    catalogs = load_catalogs()
    source_keys = set(catalogs["en"]) - {"literal"}
    errors: list[str] = []

    for locale, catalog in sorted(catalogs.items()):
        keys = set(catalog) - {"literal"}
        missing = sorted(source_keys - keys)
        extra = sorted(keys - source_keys)
        if missing:
            errors.append(f"{locale}: missing keys: {', '.join(missing)}")
        if extra:
            errors.append(f"{locale}: unexpected keys: {', '.join(extra)}")
        if locale != "en":
            missing_literals = sorted(
                set(catalogs["en"].get("literal", {})) - set(catalog.get("literal", {}))
            )
            if missing_literals:
                errors.append(f"{locale}: {len(missing_literals)} missing literal translations")
        immutable_mismatches = sorted(
            term for term in IMMUTABLE_UI_TERMS if catalog.get("literal", {}).get(term) != term
        )
        if immutable_mismatches:
            errors.append(f"{locale}: immutable UI terms changed: {', '.join(immutable_mismatches)}")
        if catalog.get("nav.prompt") != "Prompt":
            errors.append(f"{locale}: nav.prompt must remain Prompt")

    used_keys = {
        key
        for script in SCRIPTS.glob("*.js")
        for key in KEY_USE.findall(script.read_text(encoding="utf-8"))
    }
    absent = sorted(used_keys - source_keys)
    if absent:
        errors.append(f"translation keys used by JavaScript but absent from en.json: {', '.join(absent)}")

    collector = VisibleTemplateLiterals()
    for template in TEMPLATES.glob("*.html"):
        collector.feed(template.read_text(encoding="utf-8"))
    literals = set(catalogs["en"].get("literal", {}))
    untranslated = sorted(value for value in collector.values if value not in literals and value not in TECHNICAL_LITERALS)
    if untranslated:
        errors.append(f"visible template literals absent from en.json: {', '.join(untranslated)}")

    if errors:
        print("UI internationalization check failed:", *errors, sep="\n- ")
        return 1
    print(f"UI internationalization check passed ({len(catalogs)} locales, {len(source_keys)} keys).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
