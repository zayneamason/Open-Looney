"""
Tool Registry for Luna Engine
==============================

Defines the Tool protocol and ToolRegistry for managing
Luna's agentic capabilities.

Based on Part XIV (Agentic Architecture) of the Luna Engine Bible.
Tools follow MCP-compatible structure for interoperability.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Optional
import asyncio
import logging
import traceback

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class Tool:
    """
    A tool that Luna can execute.

    Tools are the bridge between Luna's reasoning and real effects
    in the world. Each tool has:

    - name: Unique identifier for the tool
    - description: Human-readable description (shown to LLM)
    - parameters: JSON Schema defining expected parameters
    - execute: Async function that performs the action
    - requires_confirmation: If True, user must approve before execution
    - timeout_seconds: Maximum execution time before abort

    Example:
        Tool(
            name="read_file",
            description="Read the contents of a file",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"}
                },
                "required": ["path"]
            },
            execute=read_file_impl,
            requires_confirmation=False,
            timeout_seconds=10
        )
    """
    name: str
    description: str
    parameters: dict  # JSON Schema
    execute: Callable[..., Awaitable[Any]]
    requires_confirmation: bool = False
    timeout_seconds: int = 30

    def to_dict(self) -> dict:
        """Convert to dictionary for LLM tool definition."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "requires_confirmation": self.requires_confirmation,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass
class ToolResult:
    """
    Result from executing a tool.

    Captures success/failure, output, and any errors.
    Used to feed tool results back into Luna's context.
    """
    success: bool
    output: Any
    error: Optional[str] = None
    execution_time_ms: float = 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "execution_time_ms": self.execution_time_ms,
        }


# =============================================================================
# TOOL REGISTRY
# =============================================================================

class ToolRegistry:
    """
    Registry for managing Luna's tools.

    The ToolRegistry is Luna's capability catalog. It:
    - Stores tool definitions
    - Provides tool lookup
    - Executes tools with timeout handling
    - Tracks execution metrics

    Usage:
        registry = ToolRegistry()
        registry.register(read_file_tool)
        registry.register(write_file_tool)

        # Execute a tool
        result = await registry.execute("read_file", {"path": "/some/file.txt"})

        # List available tools
        tools = registry.list_tools()
    """

    def __init__(self):
        """Initialize an empty tool registry."""
        self._tools: dict[str, Tool] = {}
        self._execution_count: dict[str, int] = {}
        self._error_count: dict[str, int] = {}
        logger.info("ToolRegistry initialized")

    def register(self, tool: Tool) -> None:
        """
        Register a tool with the registry.

        Args:
            tool: The Tool to register

        Raises:
            ValueError: If a tool with the same name already exists
        """
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")

        self._tools[tool.name] = tool
        self._execution_count[tool.name] = 0
        self._error_count[tool.name] = 0
        logger.debug(f"Registered tool: {tool.name}")

    def unregister(self, name: str) -> bool:
        """
        Remove a tool from the registry.

        Args:
            name: Name of the tool to remove

        Returns:
            True if tool was removed, False if not found
        """
        if name in self._tools:
            del self._tools[name]
            del self._execution_count[name]
            del self._error_count[name]
            logger.debug(f"Unregistered tool: {name}")
            return True
        return False

    def get(self, name: str) -> Optional[Tool]:
        """
        Get a tool by name.

        Args:
            name: Name of the tool to retrieve

        Returns:
            The Tool if found, None otherwise
        """
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        """
        List all registered tool names.

        Returns:
            List of tool names
        """
        return list(self._tools.keys())

    def get_all_tools(self) -> list[Tool]:
        """
        Get all registered tools.

        Returns:
            List of all Tool objects
        """
        return list(self._tools.values())

    def get_tool_definitions(self) -> list[dict]:
        """
        Get tool definitions for LLM function calling.

        Returns:
            List of tool definitions in JSON Schema format
        """
        return [tool.to_dict() for tool in self._tools.values()]

    async def execute(
        self,
        name: str,
        params: dict,
        skip_confirmation: bool = False,
    ) -> ToolResult:
        """
        Execute a tool by name with given parameters.

        Args:
            name: Name of the tool to execute
            params: Parameters to pass to the tool
            skip_confirmation: If True, bypass confirmation requirement

        Returns:
            ToolResult with success status, output, and any errors
        """
        import time
        start_time = time.time()

        # Get the tool
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                success=False,
                output=None,
                error=f"Tool '{name}' not found",
            )

        # Check confirmation requirement
        if tool.requires_confirmation and not skip_confirmation:
            return ToolResult(
                success=False,
                output=None,
                error=f"Tool '{name}' requires confirmation before execution",
            )

        # Execute with timeout
        try:
            logger.debug(f"Executing tool: {name} with params: {params}")

            # Wrap execution in timeout
            result = await asyncio.wait_for(
                tool.execute(**params),
                timeout=tool.timeout_seconds
            )

            execution_time = (time.time() - start_time) * 1000
            self._execution_count[name] += 1

            logger.debug(f"Tool {name} completed in {execution_time:.1f}ms")

            return ToolResult(
                success=True,
                output=result,
                error=None,
                execution_time_ms=execution_time,
            )

        except asyncio.TimeoutError:
            execution_time = (time.time() - start_time) * 1000
            self._error_count[name] += 1

            logger.warning(f"Tool {name} timed out after {tool.timeout_seconds}s")

            return ToolResult(
                success=False,
                output=None,
                error=f"Tool execution timed out after {tool.timeout_seconds} seconds",
                execution_time_ms=execution_time,
            )

        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            self._error_count[name] += 1

            error_msg = f"{type(e).__name__}: {str(e)}"
            logger.error(f"Tool {name} failed: {error_msg}")
            logger.debug(traceback.format_exc())

            return ToolResult(
                success=False,
                output=None,
                error=error_msg,
                execution_time_ms=execution_time,
            )

    def get_stats(self) -> dict:
        """
        Get registry statistics.

        Returns:
            Dictionary with tool counts and execution metrics
        """
        return {
            "total_tools": len(self._tools),
            "tool_names": list(self._tools.keys()),
            "execution_counts": dict(self._execution_count),
            "error_counts": dict(self._error_count),
            "tools_requiring_confirmation": [
                name for name, tool in self._tools.items()
                if tool.requires_confirmation
            ],
        }
