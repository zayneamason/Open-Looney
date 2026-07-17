"""
Clustering Service - Background service for periodic memory clustering.

This service runs in the background and periodically groups related memories
into clusters using the ClusteringEngine. Clusters help Luna:
- Organize knowledge into coherent groups
- Retrieve related memories together
- Propagate lock-in values collectively
"""

import time
import logging
import threading
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime

from luna.core.paths import memory_matrix_path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ClusteringService:
    """
    Background service for periodic clustering.

    Can be run as:
    1. Blocking foreground service (start())
    2. Background thread (start_background())
    3. Single execution (run_once())
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        interval_hours: float = 1.0
    ):
        """
        Initialize the clustering service.

        Args:
            db_path: Path to SQLite database. Defaults to the active profile's
                memory_matrix_path() — resolved at instance creation, NOT at
                class definition.
            interval_hours: Hours between clustering runs (default: 1)
        """
        self.db_path = str(db_path) if db_path else str(memory_matrix_path())
        self.interval_seconds = interval_hours * 3600
        self.running = False
        self._engine = None
        self._thread: Optional[threading.Thread] = None
        self._last_run: Optional[datetime] = None
        self._last_result: Optional[Dict] = None

    def _get_engine(self):
        """Lazy-load the clustering engine."""
        if self._engine is None:
            from luna.memory.clustering_engine import ClusteringEngine
            self._engine = ClusteringEngine(self.db_path)
        return self._engine

    def start(self) -> None:
        """
        Start the service in blocking mode.

        Runs clustering periodically until interrupted.
        """
        self.running = True
        logger.info(
            f"Clustering service starting "
            f"(interval: {self.interval_seconds/3600:.1f}h, "
            f"db: {self.db_path})"
        )

        while self.running:
            try:
                logger.info("Running clustering job...")
                engine = self._get_engine()
                result = engine.run_clustering()

                self._last_run = datetime.now()
                self._last_result = result

                logger.info(
                    f"Clustering complete: {result['clusters_created']} clusters created"
                )

                # Sleep until next run
                logger.info(
                    f"Next run in {self.interval_seconds/3600:.1f} hours"
                )
                time.sleep(self.interval_seconds)

            except KeyboardInterrupt:
                logger.info("Service stopped by user")
                self.running = False
            except Exception as e:
                logger.error(f"Clustering error: {e}", exc_info=True)
                # On error, wait 60 seconds before retry
                time.sleep(60)

    def start_background(self) -> threading.Thread:
        """
        Start the service in a background thread.

        Returns:
            The background thread
        """
        if self._thread and self._thread.is_alive():
            logger.warning("Service already running in background")
            return self._thread

        self._thread = threading.Thread(
            target=self.start,
            name="ClusteringService",
            daemon=True
        )
        self._thread.start()
        logger.info("Clustering service started in background")
        return self._thread

    def stop(self) -> None:
        """Stop the service."""
        self.running = False
        logger.info("Clustering service stop requested")

        if self._thread and self._thread.is_alive():
            # Give it a moment to clean up
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                logger.warning("Service thread did not stop cleanly")

    def run_once(self) -> Dict:
        """
        Run clustering once (for testing or manual triggers).

        Returns:
            Clustering result dict
        """
        logger.info("Running single clustering job...")
        engine = self._get_engine()
        result = engine.run_clustering()

        self._last_run = datetime.now()
        self._last_result = result

        return result

    def get_status(self) -> Dict:
        """
        Get service status.

        Returns:
            Status dict including running state and last run info
        """
        return {
            'running': self.running,
            'db_path': self.db_path,
            'interval_hours': self.interval_seconds / 3600,
            'last_run': self._last_run.isoformat() if self._last_run else None,
            'last_result': self._last_result,
            'background_thread': (
                self._thread.is_alive() if self._thread else False
            )
        }

    def get_cluster_stats(self) -> Dict:
        """
        Get current cluster statistics.

        Returns:
            Cluster statistics from ClusterManager
        """
        engine = self._get_engine()
        return engine.cluster_mgr.get_stats()


def main():
    """CLI entry point."""
    import sys

    # Parse simple args
    interval = 1.0
    once = False

    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--once":
            once = True
        elif arg == "--interval" and i < len(sys.argv) - 1:
            try:
                interval = float(sys.argv[i + 1])
            except ValueError:
                pass
        elif arg == "--help":
            print("Clustering Service - Luna Memory Economy")
            print()
            print("Usage: python -m luna.services.clustering_service [OPTIONS]")
            print()
            print("Options:")
            print("  --once        Run clustering once and exit")
            print("  --interval N  Set interval in hours (default: 1)")
            print("  --help        Show this help")
            return

    service = ClusteringService(interval_hours=interval)

    if once:
        result = service.run_once()
        print(f"\nResult: {result}")
        print(f"\nStats: {service.get_cluster_stats()}")
    else:
        try:
            service.start()
        except KeyboardInterrupt:
            print("\nService stopped")


if __name__ == "__main__":
    main()
