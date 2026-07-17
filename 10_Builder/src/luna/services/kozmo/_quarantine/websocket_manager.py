"""
KOZMO WebSocket Manager (Phase 4)

Manages WebSocket connections for real-time entity/scene updates.
Broadcasts changes to all connected clients in a project.
"""
from fastapi import WebSocket
from typing import Dict, Set
import json
import asyncio
import logging

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections per project"""

    def __init__(self):
        # project_slug -> Set[WebSocket]
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        # user_id -> WebSocket (for tracking who's editing)
        self.user_sockets: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, project_slug: str, user_id: str = None):
        """Accept and register a new WebSocket connection"""
        await websocket.accept()

        if project_slug not in self.active_connections:
            self.active_connections[project_slug] = set()

        self.active_connections[project_slug].add(websocket)

        if user_id:
            self.user_sockets[user_id] = websocket

        logger.info(f"Client connected to project {project_slug} (user: {user_id})")

    def disconnect(self, websocket: WebSocket, project_slug: str, user_id: str = None):
        """Remove WebSocket connection"""
        if project_slug in self.active_connections:
            self.active_connections[project_slug].discard(websocket)

            # Clean up empty project sets
            if not self.active_connections[project_slug]:
                del self.active_connections[project_slug]

        if user_id and user_id in self.user_sockets:
            del self.user_sockets[user_id]

        logger.info(f"Client disconnected from project {project_slug}")

    async def broadcast_to_project(
        self,
        project_slug: str,
        message: dict,
        exclude: WebSocket = None
    ):
        """Send message to all clients in a project"""
        if project_slug not in self.active_connections:
            return

        disconnected = set()

        for connection in self.active_connections[project_slug]:
            if connection == exclude:
                continue

            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error sending message: {e}")
                disconnected.add(connection)

        # Clean up dead connections
        for conn in disconnected:
            self.disconnect(conn, project_slug)

    async def send_to_user(self, user_id: str, message: dict):
        """Send message to specific user"""
        if user_id in self.user_sockets:
            try:
                await self.user_sockets[user_id].send_json(message)
            except Exception as e:
                logger.error(f"Error sending to user {user_id}: {e}")

    def get_connection_count(self, project_slug: str) -> int:
        """Get number of active connections for a project"""
        return len(self.active_connections.get(project_slug, set()))


# Global instance
manager = ConnectionManager()
