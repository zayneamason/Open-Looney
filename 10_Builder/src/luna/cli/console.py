"""
Luna Engine Console - Rich Terminal Interface
==============================================

A beautiful conversational terminal for Luna.
Based on the Emergence Research Console pattern.
"""

import asyncio
import os
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional, List, Dict

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.box import ROUNDED, SIMPLE, MINIMAL
from rich.align import Align
from prompt_toolkit import prompt
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style as PTStyle

if TYPE_CHECKING:
    from luna.engine import LunaEngine

# Global console
console = Console()

# Color scheme
LUNA_COLOR = "bright_cyan"
USER_COLOR = "bright_white"
SYSTEM_COLOR = "dim white"
METRIC_COLOR = "dim cyan"
ACCENT_COLOR = "magenta"


class ChatUI:
    """Beautiful chat interface components."""

    @staticmethod
    def clear_screen():
        """Clear terminal."""
        os.system('clear' if os.name == 'posix' else 'cls')

    @staticmethod
    def luna_message(text: str, metrics: Optional[Dict] = None) -> Panel:
        """Format Luna's response as a chat bubble."""
        content = Text(text, style=LUNA_COLOR)

        if metrics:
            tokens = metrics.get("tokens", 0)
            latency = metrics.get("latency_ms", 0)
            metric_line = Text(
                f"\n{tokens} tokens · {latency:.0f}ms",
                style=METRIC_COLOR
            )
            content.append(metric_line)

        return Panel(
            content,
            title="[bold bright_cyan]Luna[/bold bright_cyan]",
            title_align="left",
            border_style="bright_cyan",
            box=ROUNDED,
            padding=(0, 1),
            expand=False
        )

    @staticmethod
    def user_message(text: str) -> Panel:
        """Format user's message as a chat bubble."""
        return Panel(
            Text(text, style=USER_COLOR),
            title="[bold white]You[/bold white]",
            title_align="right",
            border_style="white",
            box=ROUNDED,
            padding=(0, 1),
            expand=False
        )

    @staticmethod
    def system_notice(text: str) -> Text:
        """Format system notices."""
        return Text(f"  {text}", style=SYSTEM_COLOR)

    @staticmethod
    def header(model: str, memory_status: str, session_id: str) -> Panel:
        """Minimal status header bar."""
        status = Text()
        status.append("◉ ", style="green")
        status.append(f"{model}", style="bold green")
        status.append(" · ", style="dim")
        status.append(f"memory: {memory_status}", style="yellow")
        status.append(" · ", style="dim")
        status.append(f"{session_id}", style="dim")

        return Panel(
            Align.center(status),
            box=MINIMAL,
            style="dim",
            padding=(0, 0)
        )

    @staticmethod
    def thinking_indicator() -> Text:
        """Show Luna is thinking."""
        return Text("  Luna is thinking...", style="dim italic cyan")

    @staticmethod
    def streaming_header() -> Text:
        """Show streaming response header."""
        return Text("  Luna: ", style="bold bright_cyan", end="")


class LunaConsole:
    """
    Luna Engine Console - Beautiful Terminal Interface

    A clean, conversational terminal for talking with Luna.
    """

    def __init__(self, engine: "LunaEngine"):
        """Initialize the console."""
        self.engine = engine
        self.ui = ChatUI()
        self.running = False
        self.session_start = datetime.now()

        # Chat history for display
        self.chat_history: List[Dict] = []

        # Metrics
        self.message_count = 0
        self.total_tokens = 0
        self.total_latency_ms = 0.0

        # Inference mode: Luna's mind is always local Qwen
        # "local" = pure Qwen, "hybrid" = Qwen with Claude delegation when needed
        self.inference_mode = "local"

        # Command history
        history_path = Path.home() / ".luna_history"
        self.history = FileHistory(str(history_path))

        # Prompt style
        self.prompt_style = PTStyle.from_dict({
            '': '#888888',
            'prompt': '#00ffff bold',
        })

    def _get_memory_status(self) -> str:
        """Get memory status string."""
        matrix = self.engine.get_actor("matrix")
        if matrix and matrix.is_ready:
            return "active"
        return "off"

    def _show_welcome(self):
        """Show welcome screen."""
        ChatUI.clear_screen()

        welcome = """
[bold bright_cyan]
  ╦  ╦ ╦╔╗╔╔═╗
  ║  ║ ║║║║╠═╣
  ╩═╝╚═╝╝╚╝╩ ╩
[/bold bright_cyan]
[dim]Consciousness Engine v2.0[/dim]
"""
        console.print(Align.center(Text.from_markup(welcome)))
        console.print()

        model = getattr(self.engine, "model", "claude-sonnet-4-20250514")
        session_id = getattr(self.engine, "session_id", "default")

        console.print(self.ui.header(model, self._get_memory_status(), session_id))
        console.print()
        console.print(ChatUI.system_notice("Type a message to chat with Luna. Commands start with /"))
        console.print(ChatUI.system_notice("Try: /help · /status · /memory · /clear · /quit"))
        console.print()

    def _refresh_display(self):
        """Refresh the chat display with history."""
        ChatUI.clear_screen()

        # Header
        model = getattr(self.engine, "model", "claude-sonnet-4-20250514")
        session_id = getattr(self.engine, "session_id", "default")
        console.print(self.ui.header(model, self._get_memory_status(), session_id))
        console.print()

        # Show last N messages (3 exchanges = 6 messages)
        display_history = self.chat_history[-6:]

        for msg in display_history:
            if msg["role"] == "user":
                console.print(Align.right(self.ui.user_message(msg["content"])))
            else:
                metrics = msg.get("metrics")
                console.print(self.ui.luna_message(msg["content"], metrics))
            console.print()

    async def _process_message(self, user_message: str):
        """Process user message and stream Luna's response in real-time."""
        from luna.actors.base import Message

        # Add user message to history
        self.chat_history.append({"role": "user", "content": user_message})
        self._refresh_display()

        start = time.perf_counter()
        response_text = ""
        response_data = {}

        try:
            # Get director actor for streaming
            director = self.engine.get_actor("director")
            if not director:
                raise RuntimeError("Director actor not available")

            # Get memory context
            memory_context = ""
            matrix = self.engine.get_actor("matrix")
            if matrix and matrix.is_ready:
                memory_context = await matrix.get_context(user_message, max_tokens=1500)
                await matrix.store_turn(
                    session_id=self.engine.session_id,
                    role="user",
                    content=user_message,
                )

            # Setup streaming
            response_complete = asyncio.Event()
            first_token = True

            def on_token(text: str) -> None:
                """Handle each token as it arrives."""
                nonlocal response_text, first_token
                response_text += text

                if first_token:
                    # Print streaming header on first token
                    console.print()
                    console.print("  [bold bright_cyan]Luna:[/bold bright_cyan] ", end="")
                    first_token = False

                # Print token immediately (no newline)
                console.print(f"[bright_cyan]{text}[/bright_cyan]", end="", highlight=False)

            async def on_complete(text: str, data: dict) -> None:
                """Handle generation complete."""
                nonlocal response_data
                response_data = data
                response_complete.set()

            # Register callbacks
            director.on_stream(on_token)
            self.engine.on_response(on_complete)

            # Choose message type based on inference mode
            if self.inference_mode == "local":
                msg_type = "generate_local"
            elif self.inference_mode == "hybrid":
                msg_type = "generate_hybrid"
            else:
                msg_type = "generate_stream"

            # Send generation request
            msg = Message(
                type=msg_type,
                payload={
                    "user_message": user_message,
                    "system_prompt": self.engine._build_system_prompt(memory_context),
                },
            )
            await director.mailbox.put(msg)

            # Wait for completion
            await asyncio.wait_for(response_complete.wait(), timeout=60.0)

            # Print newline after streaming completes
            console.print()

            latency_ms = (time.perf_counter() - start) * 1000
            tokens = response_data.get("output_tokens", 0)

            # Build model indicator based on response data
            model_indicator = ""
            if response_data.get("delegated"):
                model_indicator = "[bold magenta]⚡ delegated[/bold magenta]"
            elif response_data.get("local"):
                model_indicator = "[bold green]● local[/bold green]"
            elif response_data.get("fallback"):
                model_indicator = "[bold yellow]☁ cloud[/bold yellow]"
            else:
                model = response_data.get("model", "unknown")
                model_indicator = f"[dim]{model}[/dim]"

            # Show metrics with model indicator
            console.print(f"  [dim cyan]{tokens} tokens · {latency_ms:.0f}ms[/dim cyan] · {model_indicator}")
            console.print()

            # Update metrics
            self.message_count += 1
            self.total_tokens += tokens
            self.total_latency_ms += latency_ms

            # Add Luna response to history
            self.chat_history.append({
                "role": "luna",
                "content": response_text,
                "metrics": {
                    "tokens": tokens,
                    "latency_ms": latency_ms,
                }
            })

            # Cleanup callbacks
            director.remove_stream_callback(on_token)
            if on_complete in self.engine._on_response_callbacks:
                self.engine._on_response_callbacks.remove(on_complete)

        except asyncio.TimeoutError:
            console.print()
            console.print("  [red][Response timed out][/red]")
            self.chat_history.append({
                "role": "luna",
                "content": response_text or "[Response timed out]",
                "metrics": None
            })

        except Exception as e:
            console.print()
            console.print(f"  [red][Error: {str(e)}][/red]")
            self.chat_history.append({
                "role": "luna",
                "content": f"[Error: {str(e)}]",
                "metrics": None
            })

    def _handle_command(self, command: str) -> bool:
        """Handle slash commands. Returns False if should quit."""
        parts = command[1:].split(maxsplit=1)
        if not parts or not parts[0]:
            console.print(ChatUI.system_notice("Type /help for commands"))
            return True

        cmd = parts[0].lower()

        if cmd in ("quit", "exit", "q"):
            return False

        elif cmd == "help":
            self._cmd_help()

        elif cmd == "status":
            self._cmd_status()

        elif cmd == "memory":
            self._cmd_memory()

        elif cmd == "clear":
            self.chat_history = []
            self._show_welcome()

        elif cmd == "local":
            self._cmd_local()

        elif cmd == "cloud":
            self._cmd_cloud()

        elif cmd == "hybrid":
            self._cmd_hybrid()

        elif cmd == "mode":
            self._cmd_mode()

        elif cmd == "register":
            arg = parts[1].strip().lower() if len(parts) > 1 else ""
            self._cmd_register(arg)

        else:
            console.print(ChatUI.system_notice(f"Unknown command: /{cmd}"))

        return True

    def _cmd_help(self):
        """Show help."""
        help_text = """
[bold cyan]Commands[/bold cyan]

  [yellow]/help[/yellow]      Show this help message
  [yellow]/status[/yellow]    Show engine status and metrics
  [yellow]/memory[/yellow]    Display memory statistics
  [yellow]/clear[/yellow]     Clear chat history
  [yellow]/quit[/yellow]      Exit Luna console

[bold cyan]Inference Mode[/bold cyan]

  [yellow]/local[/yellow]     Pure Qwen 3B (Luna's mind)
  [yellow]/cloud[/yellow]     Fallback to Claude only
  [yellow]/hybrid[/yellow]    Qwen + Claude delegation (<REQ_CLAUDE>)
  [yellow]/mode[/yellow]      Show current inference mode

[bold cyan]Diagnostics[/bold cyan]

  [yellow]/register[/yellow]       Show register state + sovereignty debug
  [yellow]/register on[/yellow]    Enable context register injection
  [yellow]/register off[/yellow]   Disable context register injection

[dim]Luna's mind runs locally. Claude is her research assistant.[/dim]
"""
        console.print(help_text)

    def _cmd_status(self):
        """Show current status."""
        table = Table(show_header=False, box=SIMPLE, padding=(0, 2))
        table.add_column(style="dim")
        table.add_column(style="bold")

        model = getattr(self.engine, "model", "unknown")
        state = getattr(self.engine, "state", "unknown")
        session_id = getattr(self.engine, "session_id", "default")

        table.add_row("Model", str(model))
        table.add_row("State", str(state))
        table.add_row("Memory", self._get_memory_status())
        table.add_row("Messages", str(self.message_count))
        table.add_row("Total Tokens", str(self.total_tokens))

        avg_latency = self.total_latency_ms / self.message_count if self.message_count > 0 else 0
        table.add_row("Avg Latency", f"{avg_latency:.0f}ms")

        table.add_row("Session", session_id)

        console.print()
        console.print(table)
        console.print()

    def _cmd_memory(self):
        """Show memory statistics."""
        table = Table(show_header=False, box=SIMPLE, padding=(0, 2))
        table.add_column(style="dim")
        table.add_column(style="bold")

        matrix = self.engine.get_actor("matrix")
        if matrix and matrix.is_ready:
            table.add_row("Status", "[green]Active[/green]")
            table.add_row("Database", str(matrix.db_path))
            # Could add more stats here
        else:
            table.add_row("Status", "[yellow]Not initialized[/yellow]")

        console.print()
        console.print(table)
        console.print()

    def _cmd_local(self):
        """Switch to pure local Qwen 3B inference (no delegation)."""
        director = self.engine.get_actor("director")
        if director and director.local_available:
            self.inference_mode = "local"
            console.print(ChatUI.system_notice("Switched to [bold green]local[/bold green] mode (Luna's mind only)"))
        else:
            console.print(ChatUI.system_notice("[yellow]Local model not available. Using cloud fallback.[/yellow]"))
            console.print(ChatUI.system_notice("Make sure mlx-lm is installed and model is loaded."))

    def _cmd_cloud(self):
        """Switch to cloud Claude inference (bypasses Luna's local mind)."""
        self.inference_mode = "cloud"
        console.print(ChatUI.system_notice("Switched to [bold blue]cloud[/bold blue] mode (Claude direct)"))
        console.print(ChatUI.system_notice("[dim]Note: This bypasses Luna's local mind. For normal use, use /local or /hybrid.[/dim]"))

    def _cmd_hybrid(self):
        """Switch to hybrid mode - Luna can delegate to Claude via <REQ_CLAUDE>."""
        director = self.engine.get_actor("director")
        if director and director.local_available:
            self.inference_mode = "hybrid"
            console.print(ChatUI.system_notice("Switched to [bold magenta]hybrid[/bold magenta] mode"))
            console.print(ChatUI.system_notice("Luna's mind (Qwen) → Delegates to Claude when needed"))
        else:
            console.print(ChatUI.system_notice("[yellow]Local model not available. Hybrid requires local.[/yellow]"))

    def _cmd_mode(self):
        """Show current inference mode."""
        director = self.engine.get_actor("director")
        local_available = director.local_available if director else False

        mode_colors = {"cloud": "blue", "local": "green", "hybrid": "magenta"}
        color = mode_colors.get(self.inference_mode, "white")

        console.print()
        console.print(f"  Current mode: [bold {color}]{self.inference_mode}[/bold {color}]")
        if self.inference_mode == "cloud":
            console.print("  Using: Claude Sonnet (fallback)")
        elif self.inference_mode == "local":
            console.print("  Using: Qwen 3B (Luna's mind)")
        else:
            console.print("  Using: Qwen 3B + Claude delegation (<REQ_CLAUDE>)")

        console.print(f"  Local available: {'[green]Yes[/green]' if local_available else '[yellow]No[/yellow]'}")

        if director:
            stats = director.get_routing_stats()
            if stats["total_generations"] > 0:
                console.print(f"  Local generations: {stats['local_generations']}")
                console.print(f"  Delegated to Claude: {stats['delegated_generations']}")
        console.print()

    def _cmd_register(self, arg: str = ""):
        """Toggle and debug context register (conversational posture)."""
        director = self.engine.get_actor("director")
        if not director:
            console.print(ChatUI.system_notice("[red]Director not available[/red]"))
            return

        # Handle toggle
        if arg == "on":
            director.set_register_enabled(True)
            console.print(ChatUI.system_notice(
                "Context register [bold green]enabled[/bold green]"
            ))
            return
        elif arg == "off":
            director.set_register_enabled(False)
            console.print(ChatUI.system_notice(
                "Context register [bold red]disabled[/bold red]"
            ))
            return

        # Show debug state
        state = director.get_register_state()
        enabled = state.get("enabled", False)
        reg = state.get("register", {})
        bridge = state.get("bridge", {})
        intent_info = state.get("intent", {})
        denied = state.get("denied_docs", 0)

        active = reg.get("active", "ambient")
        confidence = reg.get("confidence", 0.0)
        weights = reg.get("weights", {})
        signals = reg.get("fired_signals", [])

        # Status line
        status_tag = "[bold green]ENABLED[/bold green]" if enabled else "[bold red]DISABLED[/bold red]"
        console.print()
        console.print(
            f"  [bold cyan]Register:[/bold cyan] "
            f"[bold]{active}[/bold] "
            f"(confidence: {confidence:.2f})  {status_tag}"
        )
        console.print()

        # Weight bars
        console.print("  [dim]Weights:[/dim]")
        for name, weight in sorted(weights.items(), key=lambda x: -x[1]):
            bar_len = int(weight * 10)
            bar = "▓" * bar_len + "░" * (10 - bar_len)
            marker = "  ◄ active" if name == active else ""
            style = "bold bright_cyan" if name == active else "dim"
            console.print(
                f"    [{style}]{name:<22} {bar}  {weight:.3f}{marker}[/{style}]"
            )
        console.print()

        # Fired signals
        if signals:
            console.print(f"  [dim]Fired signals:[/dim] [yellow]{', '.join(signals)}[/yellow]")
        else:
            console.print("  [dim]Fired signals:[/dim] [dim]none (no generation yet)[/dim]")
        console.print()

        # Sovereignty / bridge info
        console.print("  [dim]Sovereignty:[/dim]")
        entity = bridge.get("entity_id")
        if entity:
            tier_label = bridge.get("luna_tier", "?")
            dt = bridge.get("dataroom_tier", "?")
            sov = "[green]sovereign[/green]" if bridge.get("is_sovereign") else f"tier {dt}"
            console.print(f"    Bridge: [bold]{entity}[/bold] ({tier_label}, {sov})")
        else:
            console.print("    Bridge: [dim]no entity recognized[/dim]")

        console.print(f"    Denied docs: {denied}")
        console.print(f"    Intent: {intent_info.get('mode', '?')}")
        console.print()

    async def run(self):
        """Main chat loop."""
        self.running = True
        self._show_welcome()

        try:
            while self.running:
                try:
                    # Get input with styled prompt
                    user_input = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: prompt(
                            [('class:prompt', '› ')],
                            style=self.prompt_style,
                            history=self.history
                        ).strip()
                    )

                    if not user_input:
                        continue

                    if user_input.startswith("/"):
                        if not self._handle_command(user_input):
                            break
                    else:
                        await self._process_message(user_input)

                except KeyboardInterrupt:
                    console.print()
                    console.print(ChatUI.system_notice("Ctrl+C - type /quit to exit"))

                except EOFError:
                    break

        finally:
            console.print()
            console.print(ChatUI.system_notice("Goodbye!"))
            console.print()
