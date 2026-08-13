from __future__ import annotations

from local_meeting_ai.plugins import (
    HookContext,
    MeetingDocument,
    PluginManifest,
    PluginRegistrar,
)


class AnalysisGlossaryPlugin:
    """Small copyable example; production plugins should expose real settings."""

    manifest = PluginManifest(
        id="example.analysis-glossary",
        name="Analysis glossary example",
        version="1.0.0",
        author="Meet2Notes contributors",
        description="Demonstrates a non-destructive terminology filter before AI.",
        permissions=("read_transcript", "write_derived_artifact"),
    )

    def register(self, registrar: PluginRegistrar) -> None:
        registrar.add_filter("analysis.before", self.apply_glossary, priority=40)

    @staticmethod
    def apply_glossary(
        document: MeetingDocument,
        context: HookContext,
    ) -> MeetingDocument:
        replacements = context.plugin_settings.get("replacements", {})
        if not isinstance(replacements, dict):
            return document
        result = document.model_copy(deep=True)
        for segment in result.segments:
            for source, destination in replacements.items():
                segment.text = segment.text.replace(str(source), str(destination))
        result.metadata["glossary_filter"] = "example.analysis-glossary"
        return result


def create_plugin() -> AnalysisGlossaryPlugin:
    return AnalysisGlossaryPlugin()
