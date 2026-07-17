"""
Luna Engine Tool System
========================

The tool registry and execution system for Luna's agentic capabilities.

This module provides:
- Tool: A dataclass defining a tool Luna can execute
- ToolResult: The result of executing a tool
- ToolRegistry: Registry for managing and executing tools

Based on Part XIV (Agentic Architecture) of the Luna Engine Bible.

Usage:
    from luna.tools import Tool, ToolResult, ToolRegistry
    from luna.tools.file_tools import register_file_tools
    from luna.tools.memory_tools import register_memory_tools, set_memory_matrix

    # Create registry
    registry = ToolRegistry()

    # Register built-in tools
    register_file_tools(registry)
    register_memory_tools(registry)

    # Connect memory matrix
    set_memory_matrix(memory_matrix)

    # Execute a tool
    result = await registry.execute("read_file", {"path": "/some/file.txt"})
"""

from .registry import Tool, ToolResult, ToolRegistry
from .file_tools import (
    read_file_tool,
    write_file_tool,
    list_directory_tool,
    file_exists_tool,
    get_file_info_tool,
    register_file_tools,
    ALL_FILE_TOOLS,
)
from .memory_tools import (
    memory_query_tool,
    memory_store_tool,
    memory_context_tool,
    memory_stats_tool,
    register_memory_tools,
    set_memory_matrix,
    get_memory_matrix,
    ALL_MEMORY_TOOLS,
)

# Eden tools (optional - requires Eden adapter initialization)
try:
    from .eden_tools import (
        eden_create_image_tool,
        eden_create_video_tool,
        eden_chat_tool,
        eden_list_agents_tool,
        eden_health_tool,
        register_eden_tools,
        set_eden_adapter,
        get_eden_adapter,
        get_eden_policy,
        get_eden_policy_status,
        ALL_EDEN_TOOLS,
    )
    EDEN_TOOLS_AVAILABLE = True
except ImportError:
    EDEN_TOOLS_AVAILABLE = False
    ALL_EDEN_TOOLS = []
    def register_eden_tools(registry): pass
    def set_eden_adapter(adapter, engine=None): pass
    def get_eden_adapter(): return None
    def get_eden_policy(): return None
    def get_eden_policy_status(): return {"loaded": False}

# QA tools (optional - may not be available if QA module not installed)
try:
    from .qa_tools import (
        qa_get_last_report_tool,
        qa_get_health_tool,
        qa_search_reports_tool,
        qa_get_stats_tool,
        qa_get_assertions_tool,
        qa_add_assertion_tool,
        qa_toggle_assertion_tool,
        qa_delete_assertion_tool,
        qa_add_bug_tool,
        qa_add_bug_from_last_tool,
        qa_list_bugs_tool,
        qa_update_bug_status_tool,
        qa_get_bug_tool,
        qa_diagnose_last_tool,
        qa_check_personality_tool,
        register_qa_tools,
        ALL_QA_TOOLS,
    )
    QA_TOOLS_AVAILABLE = True
except ImportError:
    QA_TOOLS_AVAILABLE = False
    ALL_QA_TOOLS = []
    def register_qa_tools(registry): pass

# Data room tools (optional - requires data room ingestion)
try:
    from .dataroom_tools import (
        dataroom_search_tool,
        dataroom_status_tool,
        dataroom_recent_tool,
        register_dataroom_tools,
        ALL_DATAROOM_TOOLS,
    )
    DATAROOM_TOOLS_AVAILABLE = True
except ImportError:
    DATAROOM_TOOLS_AVAILABLE = False
    ALL_DATAROOM_TOOLS = []
    def register_dataroom_tools(registry): pass

# Aperture & Library Cognition tools
try:
    from .aperture_tools import (
        aperture_get_tool,
        aperture_set_tool,
        collection_lock_in_tool,
        annotate_tool,
        register_aperture_tools,
        set_aperture_manager,
        get_aperture_manager,
        set_lock_in_engine as set_aperture_lock_in_engine,
        get_lock_in_engine as get_aperture_lock_in_engine,
        set_annotation_engine,
        get_annotation_engine,
        ALL_APERTURE_TOOLS,
    )
    APERTURE_TOOLS_AVAILABLE = True
except ImportError:
    APERTURE_TOOLS_AVAILABLE = False
    ALL_APERTURE_TOOLS = []
    def register_aperture_tools(registry): pass
    def set_aperture_manager(m): pass
    def get_aperture_manager(): return None
    def set_aperture_lock_in_engine(e): pass
    def get_aperture_lock_in_engine(): return None
    def set_annotation_engine(e): pass
    def get_annotation_engine(): return None

__all__ = [
    # Core classes
    "Tool",
    "ToolResult",
    "ToolRegistry",
    # File tools
    "read_file_tool",
    "write_file_tool",
    "list_directory_tool",
    "file_exists_tool",
    "get_file_info_tool",
    "register_file_tools",
    "ALL_FILE_TOOLS",
    # Memory tools
    "memory_query_tool",
    "memory_store_tool",
    "memory_context_tool",
    "memory_stats_tool",
    "register_memory_tools",
    "set_memory_matrix",
    "get_memory_matrix",
    "ALL_MEMORY_TOOLS",
    # Eden tools
    "register_eden_tools",
    "set_eden_adapter",
    "get_eden_adapter",
    "get_eden_policy",
    "get_eden_policy_status",
    "ALL_EDEN_TOOLS",
    "EDEN_TOOLS_AVAILABLE",
    # QA tools
    "register_qa_tools",
    "ALL_QA_TOOLS",
    "QA_TOOLS_AVAILABLE",
    # Data room tools
    "register_dataroom_tools",
    "ALL_DATAROOM_TOOLS",
    "DATAROOM_TOOLS_AVAILABLE",
    # Aperture tools
    "register_aperture_tools",
    "set_aperture_manager",
    "get_aperture_manager",
    "set_aperture_lock_in_engine",
    "get_aperture_lock_in_engine",
    "set_annotation_engine",
    "get_annotation_engine",
    "ALL_APERTURE_TOOLS",
    "APERTURE_TOOLS_AVAILABLE",
]


def create_default_registry() -> ToolRegistry:
    """
    Create a ToolRegistry with all default tools registered.

    Note: Memory tools require set_memory_matrix() to be called
    before they can be used.

    Returns:
        ToolRegistry with all built-in tools
    """
    registry = ToolRegistry()
    register_file_tools(registry)
    register_memory_tools(registry)
    register_eden_tools(registry)  # Will no-op if Eden not available
    register_qa_tools(registry)  # Will no-op if QA not available
    register_dataroom_tools(registry)  # Will no-op if not available
    register_aperture_tools(registry)  # Will no-op if not available
    return registry
