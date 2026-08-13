from __future__ import annotations

import time

import pytest

from local_meeting_ai.application.rag_vector_store import RagVectorStoreGateway
from local_meeting_ai.infrastructure.database.connection import Database
from local_meeting_ai.infrastructure.database.migrations import MigrationRunner
from local_meeting_ai.infrastructure.database.rag_repository import RagRepository
from local_meeting_ai.infrastructure.database.repositories import (
    PluginExecutionRepository,
    SettingsRepository,
)
from local_meeting_ai.plugins.contracts import (
    HookContext,
    MeetingDocument,
    PluginManifest,
    TranscriptDocumentSegment,
    VectorStoreCatalog,
    VectorStoreDescriptor,
    VectorStoreOperation,
)
from local_meeting_ai.plugins.manager import HookBus, PluginManager, PluginRegistrar


def _document() -> MeetingDocument:
    return MeetingDocument(
        meeting_id=1,
        transcription_id=2,
        source_language="es",
        analysis_language="es",
        segments=[
            TranscriptDocumentSegment(
                id=3,
                index=0,
                start_ms=0,
                end_ms=500,
                text="  Hola    a todos.  ",
                speaker_label="Speaker 1",
            )
        ],
    )


@pytest.fixture
def plugin_services(tmp_path):  # type: ignore[no-untyped-def]
    database = Database(tmp_path / "plugins.db")
    MigrationRunner(database).apply()
    settings = SettingsRepository(database)
    executions = PluginExecutionRepository(database)
    return settings, executions


@pytest.mark.asyncio
async def test_builtin_filter_is_non_destructive_and_audited(plugin_services) -> None:  # type: ignore[no-untyped-def]
    settings, executions = plugin_services
    manager = PluginManager(settings, executions)
    original = _document()

    filtered = await manager.hooks.apply_filters(
        "analysis.before",
        original,
        HookContext(
            hook="analysis.before",
            pipeline_id="pipeline-1",
            job_uuid="job-1",
        ),
    )

    assert isinstance(filtered, MeetingDocument)
    assert original.segments[0].text == "  Hola    a todos.  "
    assert filtered.segments[0].text == "Hola a todos."
    assert filtered.metadata["analysis_cleanup_segments"] == 1
    audit = executions.recent()
    assert audit[0]["plugin_id"] == "meet2notes.analysis-cleanup"
    assert audit[0]["status"] == "completed"
    assert audit[0]["input_digest"] != audit[0]["output_digest"]
    assert "Hola" not in str(audit[0])


@pytest.mark.asyncio
async def test_disabling_plugin_removes_its_hooks(plugin_services) -> None:  # type: ignore[no-untyped-def]
    settings, executions = plugin_services
    manager = PluginManager(settings, executions)

    disabled = manager.set_enabled("meet2notes.analysis-cleanup", False)
    result = await manager.hooks.apply_filters(
        "analysis.before",
        _document(),
        HookContext(hook="analysis.before"),
    )

    assert disabled["enabled"] is False
    assert result.segments[0].text == "  Hola    a todos.  "
    assert executions.recent() == []


@pytest.mark.asyncio
async def test_optional_plugin_failure_is_recorded_and_pipeline_continues(
    plugin_services,
) -> None:  # type: ignore[no-untyped-def]
    _settings, executions = plugin_services
    bus = HookBus(executions, lambda _plugin_id: {})
    manifest = PluginManifest(
        id="test.failing-filter",
        name="Failing test filter",
        version="1.0.0",
        description="Used by the hook failure policy test.",
    )
    registrar = PluginRegistrar(manifest, bus.registrations)

    def fail(document: MeetingDocument, context: HookContext) -> MeetingDocument:
        del document, context
        raise RuntimeError("expected plugin failure")

    registrar.add_filter("analysis.before", fail, failure_policy="continue")
    original = _document()
    result = await bus.apply_filters(
        "analysis.before",
        original,
        HookContext(hook="analysis.before", job_uuid="job-2"),
    )

    assert result is original
    assert executions.recent()[0]["status"] == "failed"
    assert executions.recent()[0]["message"] == "expected plugin failure"


@pytest.mark.asyncio
async def test_sync_plugin_timeout_does_not_block_or_replace_the_artifact(
    plugin_services,
) -> None:  # type: ignore[no-untyped-def]
    _settings, executions = plugin_services
    bus = HookBus(executions, lambda _plugin_id: {})
    manifest = PluginManifest(
        id="test.slow-filter",
        name="Slow test filter",
        version="1.0.0",
        description="Used by the synchronous timeout test.",
    )
    registrar = PluginRegistrar(manifest, bus.registrations)

    def slow(document: MeetingDocument, context: HookContext) -> MeetingDocument:
        del context
        time.sleep(0.4)
        return document

    registrar.add_filter(
        "analysis.before",
        slow,
        timeout_seconds=0.1,
        failure_policy="continue",
    )
    original = _document()
    started = time.perf_counter()
    result = await bus.apply_filters(
        "analysis.before",
        original,
        HookContext(hook="analysis.before"),
    )

    assert time.perf_counter() - started < 0.3
    assert result is original
    assert executions.recent()[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_vector_store_plugin_can_register_and_handle_a_destination(
    plugin_services,
) -> None:  # type: ignore[no-untyped-def]
    settings, executions = plugin_services
    manager = PluginManager(settings, executions)
    registrar = PluginRegistrar(
        PluginManifest(
            id="test.vector-store",
            name="Test vector store",
            version="1.0.0",
            description="Exercises the public RAG vector-store hooks.",
        ),
        manager.hooks.registrations,
    )

    def add_store(
        catalog: VectorStoreCatalog,
        context: HookContext,
    ) -> VectorStoreCatalog:
        del context
        return VectorStoreCatalog(
            stores=[
                *catalog.stores,
                VectorStoreDescriptor(
                    id="test-memory",
                    display_name="Test memory",
                    description="An in-memory vector-store test adapter.",
                    plugin_id="test.vector-store",
                ),
            ]
        )

    def handle_store(
        command: VectorStoreOperation,
        context: HookContext,
    ) -> VectorStoreOperation:
        del context
        if command.store_id != "test-memory" or command.operation != "counts":
            return command
        return command.model_copy(
            update={
                "handled": True,
                "result": {"chunks": 7, "meetings": 2, "transcriptions": 2},
            }
        )

    registrar.add_filter("rag.vector_store.catalog", add_store)
    registrar.add_filter("rag.vector_store.operation", handle_store)
    gateway = RagVectorStoreGateway(RagRepository(executions.database), manager)

    catalog = await gateway.catalog()
    counts = await gateway.counts("test-memory")

    assert [store["id"] for store in catalog] == ["sqlite", "test-memory"]
    assert counts == {"chunks": 7, "meetings": 2, "transcriptions": 2}
