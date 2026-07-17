"""
Background service that updates cluster lock-in values periodically.

This service runs in a loop, recalculating lock-in for all clusters
based on:
- Access patterns (logarithmic boost)
- Time-based decay (state-dependent rates)
- Member node lock-in averages
- Edge strength to other clusters

Usage:
    # Single run (for testing)
    python -m luna.services.lockin_service --once

    # Continuous background service
    python -m luna.services.lockin_service

    # Custom interval (10 minutes)
    python -m luna.services.lockin_service --interval 10
"""

import time
import logging
import argparse
import signal
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

from luna.core.paths import memory_matrix_path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class LockInService:
    """
    Background service for periodic lock-in updates.

    The service maintains cluster lock-in values by:
    1. Periodically recalculating lock-in using Gemini's corrected formula
    2. Applying time-based decay (crystallized clusters decay slowest)
    3. Detecting and logging state transitions

    Attributes:
        db_path: Path to the Luna Engine SQLite database
        interval_seconds: Time between update cycles
        running: Flag to control the service loop
        calculator: LockInCalculator instance (lazy-loaded)
    """

    def __init__(self, db_path: str, interval_minutes: int = 5):
        """
        Initialize the lock-in service.

        Args:
            db_path: Path to luna_engine.db
            interval_minutes: Minutes between update cycles (default 5)
        """
        self.db_path = db_path
        self.interval_seconds = interval_minutes * 60
        self.running = False
        self._calculator = None
        self._stats = {
            'cycles_completed': 0,
            'total_updates': 0,
            'total_state_changes': 0,
            'total_errors': 0,
            'started_at': None,
            'last_cycle_at': None,
        }

    @property
    def calculator(self):
        """Lazy-load the calculator to avoid import issues."""
        if self._calculator is None:
            from luna.memory.lock_in import LockInCalculator
            self._calculator = LockInCalculator(self.db_path)
        return self._calculator

    def start(self):
        """
        Start the lock-in update service.

        This method blocks and runs until stop() is called or
        a KeyboardInterrupt is received.
        """
        self.running = True
        self._stats['started_at'] = datetime.now().isoformat()

        logger.info(f"Lock-in service starting")
        logger.info(f"  Database: {self.db_path}")
        logger.info(f"  Interval: {self.interval_seconds / 60:.1f} minutes")

        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        while self.running:
            try:
                self._run_cycle()

                if self.running:
                    logger.debug(f"Sleeping for {self.interval_seconds} seconds...")
                    # Sleep in small increments to allow for quick shutdown
                    for _ in range(int(self.interval_seconds)):
                        if not self.running:
                            break
                        time.sleep(1)

            except KeyboardInterrupt:
                logger.info("Service stopped by user (Ctrl+C)")
                self.running = False
            except Exception as e:
                logger.error(f"Error in lock-in update cycle: {e}", exc_info=True)
                self._stats['total_errors'] += 1
                # Sleep shorter on error to retry sooner
                time.sleep(60)

        self._log_final_stats()

    def stop(self):
        """Stop the service gracefully."""
        self.running = False
        logger.info("Lock-in service stop requested")

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.stop()

    def _run_cycle(self):
        """Run a single update cycle."""
        cycle_start = time.time()
        logger.info("Starting lock-in update cycle...")

        try:
            result = self.calculator.update_all_clusters()

            # Update stats
            self._stats['cycles_completed'] += 1
            self._stats['total_updates'] += result.get('updated', 0)
            self._stats['total_state_changes'] += len(result.get('state_changes', []))
            self._stats['total_errors'] += result.get('errors', 0)
            self._stats['last_cycle_at'] = datetime.now().isoformat()

            # Log results
            cycle_duration = time.time() - cycle_start
            logger.info(
                f"Cycle complete: {result['updated']}/{result['total']} clusters "
                f"updated in {cycle_duration:.2f}s"
            )

            if result.get('state_changes'):
                logger.info(f"State transitions: {len(result['state_changes'])}")
                for change in result['state_changes']:
                    logger.info(
                        f"  {change['name'][:40]}: "
                        f"{change['from']} -> {change['to']}"
                    )

            if result.get('errors', 0) > 0:
                logger.warning(f"Errors encountered: {result['errors']}")

        except Exception as e:
            logger.error(f"Cycle failed: {e}", exc_info=True)
            self._stats['total_errors'] += 1

    def run_once(self) -> dict:
        """
        Run update once (for testing/manual triggering).

        Returns:
            Result dict from update_all_clusters()
        """
        logger.info("Running single lock-in update cycle...")
        result = self.calculator.update_all_clusters()

        logger.info(f"Updated {result['updated']}/{result['total']} clusters")
        if result.get('state_changes'):
            for change in result['state_changes']:
                logger.info(
                    f"  State change: {change['name']} "
                    f"{change['from']} -> {change['to']}"
                )

        return result

    def get_stats(self) -> dict:
        """Get service statistics."""
        return {
            **self._stats,
            'running': self.running,
            'interval_minutes': self.interval_seconds / 60,
        }

    def _log_final_stats(self):
        """Log final statistics on shutdown."""
        logger.info("=== Lock-in Service Final Stats ===")
        logger.info(f"  Cycles completed: {self._stats['cycles_completed']}")
        logger.info(f"  Total updates: {self._stats['total_updates']}")
        logger.info(f"  State changes: {self._stats['total_state_changes']}")
        logger.info(f"  Errors: {self._stats['total_errors']}")
        if self._stats['started_at']:
            logger.info(f"  Started: {self._stats['started_at']}")
        if self._stats['last_cycle_at']:
            logger.info(f"  Last cycle: {self._stats['last_cycle_at']}")


def main():
    """Main entry point for the lock-in service."""
    parser = argparse.ArgumentParser(
        description='Background service for Memory Economy lock-in updates'
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='Run once and exit (for testing)'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=5,
        help='Update interval in minutes (default: 5)'
    )
    parser.add_argument(
        '--db',
        type=str,
        help='Path to memory_matrix.lun (default: data/user/memory_matrix.lun)'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose (DEBUG) logging'
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Determine database path
    if args.db:
        db_path = Path(args.db)
    else:
        # Default: canonical memory_matrix_path()
        db_path = memory_matrix_path()

    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        sys.exit(1)

    # Create and run service
    service = LockInService(str(db_path), interval_minutes=args.interval)

    if args.once:
        result = service.run_once()
        print(f"\nResult: {result}")
    else:
        service.start()


if __name__ == "__main__":
    main()
