"""
Luna Debug CLI - Self-service diagnostic tools.

Commands:
    luna-debug health         - Full system health check
    luna-debug find-person    - Diagnose person lookup issues
    luna-debug stats          - Database statistics
    luna-debug search         - Search memory for content
    luna-debug recent         - Recent activity summary
    luna-debug extraction     - Extraction pipeline status
"""

import argparse
import sys
import json
from pathlib import Path
from typing import Optional

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.box import ROUNDED
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from luna.diagnostics import HealthChecker, HealthStatus


# Initialize console
console = Console() if RICH_AVAILABLE else None


def print_plain(text: str):
    """Fallback print for when Rich isn't available."""
    print(text)


def status_color(status: HealthStatus) -> str:
    """Get color for health status."""
    return {
        HealthStatus.HEALTHY: "green",
        HealthStatus.DEGRADED: "yellow",
        HealthStatus.BROKEN: "red",
        HealthStatus.UNKNOWN: "dim"
    }.get(status, "white")


def status_icon(status: HealthStatus) -> str:
    """Get icon for health status."""
    return {
        HealthStatus.HEALTHY: "[green]✓[/green]",
        HealthStatus.DEGRADED: "[yellow]![/yellow]",
        HealthStatus.BROKEN: "[red]✗[/red]",
        HealthStatus.UNKNOWN: "[dim]?[/dim]"
    }.get(status, " ")


def cmd_health(args):
    """Run full health check."""
    checker = HealthChecker(args.db)
    checks = checker.check_all()

    if RICH_AVAILABLE and not args.json:
        console.print()
        console.print(Panel.fit(
            "[bold cyan]Luna Engine Health Check[/bold cyan]",
            border_style="cyan"
        ))
        console.print()

        table = Table(show_header=True, header_style="bold", box=ROUNDED)
        table.add_column("Component", style="cyan")
        table.add_column("Status", justify="center")
        table.add_column("Message")

        for check in checks:
            color = status_color(check.status)
            icon = status_icon(check.status)
            table.add_row(
                check.component,
                f"{icon} [{color}]{check.status.value}[/{color}]",
                check.message
            )

        console.print(table)
        console.print()

        # Show summary
        healthy = sum(1 for c in checks if c.status == HealthStatus.HEALTHY)
        degraded = sum(1 for c in checks if c.status == HealthStatus.DEGRADED)
        broken = sum(1 for c in checks if c.status == HealthStatus.BROKEN)

        summary = f"[green]{healthy} healthy[/green]"
        if degraded:
            summary += f" | [yellow]{degraded} degraded[/yellow]"
        if broken:
            summary += f" | [red]{broken} broken[/red]"

        console.print(f"Summary: {summary}")
        console.print()

        # Show detailed metrics if verbose
        if args.verbose:
            for check in checks:
                if check.metrics:
                    console.print(f"[dim]{check.component}:[/dim]")
                    for key, value in check.metrics.items():
                        console.print(f"  {key}: {value}")
                    console.print()
    else:
        # JSON output
        output = {
            'checks': [
                {
                    'component': c.component,
                    'status': c.status.value,
                    'message': c.message,
                    'metrics': c.metrics,
                    'timestamp': c.timestamp
                }
                for c in checks
            ]
        }
        print(json.dumps(output, indent=2))


def cmd_find_person(args):
    """Diagnose person lookup issues."""
    if not args.name:
        print("Error: Please provide a name to search for")
        print("Usage: luna-debug find-person <name>")
        sys.exit(1)

    checker = HealthChecker(args.db)
    results = checker.find_person(args.name)

    if RICH_AVAILABLE and not args.json:
        console.print()

        if results['found']:
            console.print(Panel.fit(
                f"[bold green]Found '{args.name}'[/bold green]",
                border_style="green"
            ))
        else:
            console.print(Panel.fit(
                f"[bold red]'{args.name}' NOT FOUND[/bold red]",
                border_style="red"
            ))

        console.print()

        # Show search results
        if 'entities' in results.get('search_results', {}):
            console.print("[bold cyan]Entities:[/bold cyan]")
            for entity in results['search_results']['entities']:
                console.print(f"  ID: {entity['id']}")
                console.print(f"  Type: {entity['type']}")
                console.print(f"  Name: {entity['name']}")
                if entity['aliases']:
                    console.print(f"  Aliases: {entity['aliases']}")
                if entity['core_facts']:
                    console.print(f"  Facts: {entity['core_facts']}")
                console.print()

        if 'memory_nodes' in results.get('search_results', {}):
            console.print("[bold cyan]Memory Nodes:[/bold cyan]")
            table = Table(show_header=True, box=ROUNDED)
            table.add_column("ID", style="dim")
            table.add_column("Type")
            table.add_column("Content", max_width=60)
            table.add_column("Lock-in")

            for node in results['search_results']['memory_nodes'][:5]:
                table.add_row(
                    node['id'][:8] + '...',
                    node['type'],
                    node['content_preview'],
                    f"{node['lock_in']:.2f}"
                )
            console.print(table)
            console.print()

        # Show diagnosis
        if results['diagnosis']:
            console.print("[bold yellow]Diagnosis:[/bold yellow]")
            for d in results['diagnosis']:
                console.print(f"  - {d}")
            console.print()

        # Show suggestions
        if results['suggestions']:
            console.print("[bold cyan]Suggestions:[/bold cyan]")
            for s in results['suggestions']:
                console.print(f"  - {s}")
            console.print()
    else:
        print(json.dumps(results, indent=2))


def cmd_stats(args):
    """Show database statistics."""
    checker = HealthChecker(args.db)

    # Get detailed metrics from each check
    db_check = checker.check_database()
    matrix_check = checker.check_memory_matrix()
    entity_check = checker.check_entities()
    session_check = checker.check_sessions()

    if RICH_AVAILABLE and not args.json:
        console.print()
        console.print(Panel.fit(
            "[bold cyan]Luna Engine Statistics[/bold cyan]",
            border_style="cyan"
        ))
        console.print()

        # Database stats
        console.print("[bold]Database[/bold]")
        db = db_check.metrics
        console.print(f"  Size: {db.get('db_size_mb', 0):.1f} MB")
        console.print(f"  Tables: {db.get('tables', 0)}")
        console.print(f"  Nodes: {db.get('node_count', 0):,}")
        console.print()

        # Memory Matrix stats
        console.print("[bold]Memory Matrix[/bold]")
        mm = matrix_check.metrics
        console.print(f"  Total nodes: {mm.get('total_nodes', 0):,}")
        console.print(f"  Edges: {mm.get('edge_count', 0):,}")
        console.print(f"  Avg lock-in: {mm.get('avg_lock_in', 0):.3f}")

        if mm.get('node_types'):
            console.print("  Node types:")
            for ntype, count in mm['node_types'].items():
                console.print(f"    {ntype}: {count:,}")

        if mm.get('lock_in_states'):
            console.print("  Lock-in states:")
            for state, count in mm['lock_in_states'].items():
                console.print(f"    {state}: {count:,}")
        console.print()

        # Entity stats
        console.print("[bold]Entities[/bold]")
        ent = entity_check.metrics
        console.print(f"  Total: {ent.get('total_entities', 0)}")
        console.print(f"  People: {ent.get('person_count', 0)}")
        console.print(f"  Mentions: {ent.get('mention_count', 0):,}")
        console.print(f"  Relationships: {ent.get('relationship_count', 0)}")

        if ent.get('entity_types'):
            console.print("  By type:")
            for etype, count in ent['entity_types'].items():
                console.print(f"    {etype}: {count}")
        console.print()

        # Session stats
        console.print("[bold]Sessions[/bold]")
        sess = session_check.metrics
        console.print(f"  Total sessions: {sess.get('total_sessions', 0)}")
        console.print(f"  Recent (24h): {sess.get('recent_sessions_24h', 0)}")
        console.print(f"  Total turns: {sess.get('total_turns', 0):,}")
        console.print(f"  Recent turns (1h): {sess.get('recent_turns_1h', 0)}")
        console.print()
    else:
        output = {
            'database': db_check.metrics,
            'memory_matrix': matrix_check.metrics,
            'entities': entity_check.metrics,
            'sessions': session_check.metrics
        }
        print(json.dumps(output, indent=2))


def cmd_search(args):
    """Search memory for content."""
    if not args.query:
        print("Error: Please provide a search query")
        print("Usage: luna-debug search <query>")
        sys.exit(1)

    import sqlite3

    from luna.core.paths import memory_matrix_path
    db_path = args.db or str(memory_matrix_path())

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout=15000")
    cursor = conn.cursor()

    # Search memory nodes
    cursor.execute("""
        SELECT id, node_type, content, lock_in, lock_in_state, created_at
        FROM memory_nodes
        WHERE LOWER(content) LIKE ?
        ORDER BY lock_in DESC
        LIMIT ?
    """, (f'%{args.query.lower()}%', args.limit))

    results = cursor.fetchall()
    conn.close()

    if RICH_AVAILABLE and not args.json:
        console.print()
        console.print(f"[bold cyan]Search: '{args.query}'[/bold cyan]")
        console.print(f"[dim]Found {len(results)} results[/dim]")
        console.print()

        if results:
            table = Table(show_header=True, box=ROUNDED)
            table.add_column("Type", style="cyan")
            table.add_column("Content", max_width=70)
            table.add_column("Lock-in")
            table.add_column("State")

            for r in results:
                content = r[2][:100] + '...' if len(r[2]) > 100 else r[2]
                table.add_row(
                    r[1],
                    content,
                    f"{r[3]:.2f}",
                    r[4]
                )

            console.print(table)
        else:
            console.print("[yellow]No results found[/yellow]")
        console.print()
    else:
        output = {
            'query': args.query,
            'count': len(results),
            'results': [
                {
                    'id': r[0],
                    'type': r[1],
                    'content': r[2],
                    'lock_in': r[3],
                    'state': r[4],
                    'created': r[5]
                }
                for r in results
            ]
        }
        print(json.dumps(output, indent=2))


def cmd_recent(args):
    """Show recent activity."""
    checker = HealthChecker(args.db)
    activity = checker.get_recent_activity(hours=args.hours)

    if RICH_AVAILABLE and not args.json:
        console.print()
        console.print(Panel.fit(
            f"[bold cyan]Recent Activity ({args.hours}h)[/bold cyan]",
            border_style="cyan"
        ))
        console.print()

        console.print(f"  Nodes created: {activity.get('nodes_created', 0)}")
        console.print(f"  Turns added: {activity.get('turns_added', 0)}")
        console.print(f"  Entities updated: {activity.get('entities_updated', 0)}")

        if activity.get('recent_node_types'):
            console.print()
            console.print("[bold]Recent node types:[/bold]")
            for ntype, count in activity['recent_node_types'].items():
                console.print(f"    {ntype}: {count}")

        if activity.get('recent_sessions'):
            console.print()
            console.print("[bold]Recent sessions:[/bold]")
            table = Table(show_header=True, box=ROUNDED)
            table.add_column("Session ID")
            table.add_column("Turns")
            table.add_column("Started")

            for sess in activity['recent_sessions']:
                table.add_row(
                    sess['session_id'][:20] + '...' if len(sess['session_id']) > 20 else sess['session_id'],
                    str(sess['turns']),
                    str(sess['started'])
                )
            console.print(table)

        console.print()
    else:
        print(json.dumps(activity, indent=2))


def cmd_extraction(args):
    """Show extraction pipeline status."""
    checker = HealthChecker(args.db)
    check = checker.check_extraction()

    if RICH_AVAILABLE and not args.json:
        console.print()
        icon = status_icon(check.status)
        color = status_color(check.status)

        console.print(Panel.fit(
            f"[bold cyan]Extraction Pipeline[/bold cyan]\n"
            f"{icon} [{color}]{check.status.value.upper()}[/{color}]",
            border_style="cyan"
        ))
        console.print()

        console.print(f"  {check.message}")
        console.print()

        metrics = check.metrics
        console.print("[bold]Metrics:[/bold]")
        console.print(f"  Recent nodes (1h): {metrics.get('recent_nodes_1h', 0)}")
        console.print(f"  Today's nodes: {metrics.get('today_nodes', 0)}")
        console.print(f"  Total nodes: {metrics.get('total_nodes', 0):,}")
        console.print(f"  Pending extractions: {metrics.get('pending_extractions', 0)}")
        console.print()
    else:
        output = {
            'status': check.status.value,
            'message': check.message,
            'metrics': check.metrics
        }
        print(json.dumps(output, indent=2))


def main():
    """Main entry point for luna-debug CLI."""
    parser = argparse.ArgumentParser(
        prog='luna-debug',
        description='Luna Engine diagnostic tools'
    )

    parser.add_argument(
        '--db',
        type=str,
        default=None,
        help='Path to database (default: data/user/memory_matrix.lun)'
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='Output as JSON'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Verbose output'
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # health command
    health_parser = subparsers.add_parser('health', help='Full system health check')
    health_parser.set_defaults(func=cmd_health)

    # find-person command
    person_parser = subparsers.add_parser('find-person', help='Diagnose person lookup')
    person_parser.add_argument('name', nargs='?', help='Person name to search for')
    person_parser.set_defaults(func=cmd_find_person)

    # stats command
    stats_parser = subparsers.add_parser('stats', help='Database statistics')
    stats_parser.set_defaults(func=cmd_stats)

    # search command
    search_parser = subparsers.add_parser('search', help='Search memory content')
    search_parser.add_argument('query', nargs='?', help='Search query')
    search_parser.add_argument('-n', '--limit', type=int, default=20, help='Max results')
    search_parser.set_defaults(func=cmd_search)

    # recent command
    recent_parser = subparsers.add_parser('recent', help='Recent activity summary')
    recent_parser.add_argument('--hours', type=int, default=24, help='Hours to look back')
    recent_parser.set_defaults(func=cmd_recent)

    # extraction command
    extraction_parser = subparsers.add_parser('extraction', help='Extraction pipeline status')
    extraction_parser.set_defaults(func=cmd_extraction)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == '__main__':
    main()
