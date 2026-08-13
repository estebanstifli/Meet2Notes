"""Public extension API for Meet2Notes plugins.

Community packages should import contracts from this module rather than from
application or infrastructure internals. The API is versioned independently so
plugins can fail with a clear compatibility message after a future upgrade.
"""

from .contracts import (
    PLUGIN_API_VERSION,
    AnalysisArtifact,
    HookContext,
    MeetingDocument,
    PluginManifest,
    TranscriptDocumentSegment,
    VectorStoreCatalog,
    VectorStoreDescriptor,
    VectorStoreOperation,
)
from .manager import PluginRegistrar

__all__ = [
    "PLUGIN_API_VERSION",
    "AnalysisArtifact",
    "HookContext",
    "MeetingDocument",
    "PluginManifest",
    "PluginRegistrar",
    "TranscriptDocumentSegment",
    "VectorStoreCatalog",
    "VectorStoreDescriptor",
    "VectorStoreOperation",
]
