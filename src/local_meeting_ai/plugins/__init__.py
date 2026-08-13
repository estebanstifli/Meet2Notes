"""Public extension API for Meet2Notes plugins.

Community packages should import contracts from this module rather than from
application or infrastructure internals. The API is versioned independently so
plugins can fail with a clear compatibility message after a future upgrade.
"""

from local_meeting_ai.domain.entities import (
    DiarizationSegment,
    SegmentDraft,
    SummaryResult,
    TranscriptionEngineRequest,
    TranscriptionResult,
)
from local_meeting_ai.domain.protocols import (
    CancellationCheck,
    DiarizationEngine,
    EmbeddingProvider,
    ProgressReporter,
    SegmentReporter,
    SummaryEngine,
    TranscriptionEngine,
)

from .contracts import (
    PLUGIN_API_VERSION,
    AnalysisArtifact,
    HookContext,
    MeetingDocument,
    PluginManifest,
    PluginSettingField,
    ProviderDescriptor,
    ProviderKind,
    ProviderModel,
    ProviderRuntimeContext,
    TranscriptDocumentSegment,
    VectorStoreCatalog,
    VectorStoreDescriptor,
    VectorStoreOperation,
)
from .manager import PluginRegistrar
from .providers import ProviderFactory

__all__ = [
    "PLUGIN_API_VERSION",
    "AnalysisArtifact",
    "CancellationCheck",
    "DiarizationEngine",
    "DiarizationSegment",
    "EmbeddingProvider",
    "HookContext",
    "MeetingDocument",
    "PluginManifest",
    "PluginRegistrar",
    "PluginSettingField",
    "ProgressReporter",
    "ProviderDescriptor",
    "ProviderFactory",
    "ProviderKind",
    "ProviderModel",
    "ProviderRuntimeContext",
    "SegmentDraft",
    "SegmentReporter",
    "SummaryEngine",
    "SummaryResult",
    "TranscriptDocumentSegment",
    "TranscriptionEngine",
    "TranscriptionEngineRequest",
    "TranscriptionResult",
    "VectorStoreCatalog",
    "VectorStoreDescriptor",
    "VectorStoreOperation",
]
