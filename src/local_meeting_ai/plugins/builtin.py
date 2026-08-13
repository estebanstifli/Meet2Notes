from __future__ import annotations

import re

from .contracts import HookContext, MeetingDocument, PluginManifest
from .manager import PluginRegistrar


class AnalysisCleanupPlugin:
    """Harmless reference plugin used to exercise the public filter API."""

    manifest = PluginManifest(
        id="meet2notes.analysis-cleanup",
        name="Analysis transcript cleanup",
        version="1.0.0",
        author="Meet2Notes contributors",
        description=(
            "Normalizes repeated whitespace in the temporary transcript sent "
            "to AI without changing the saved transcription."
        ),
        permissions=("read_transcript", "write_derived_artifact"),
        default_enabled=True,
    )

    def register(self, registrar: PluginRegistrar) -> None:
        registrar.add_filter(
            "analysis.before",
            self.cleanup,
            priority=20,
            timeout_seconds=10,
            failure_policy="continue",
        )

    @staticmethod
    def cleanup(document: MeetingDocument, context: HookContext) -> MeetingDocument:
        del context
        transformed = document.model_copy(deep=True)
        changed = 0
        for segment in transformed.segments:
            clean = re.sub(r"\s+", " ", segment.text).strip()
            if clean != segment.text:
                segment.text = clean
                changed += 1
        transformed.metadata["analysis_cleanup_segments"] = changed
        return transformed


BUILTIN_PLUGINS = (AnalysisCleanupPlugin(),)
