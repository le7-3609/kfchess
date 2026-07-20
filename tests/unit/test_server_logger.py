"""Unit tests for ServerLogger and ClientLogIngestor."""

import os
import pytest
from shared.events import GameStartedEvent, ScoreUpdatedEvent
from server.infrastructure.logging.client_logger import ClientLogIngestor
from server.infrastructure.logging.server_logger import ServerLogger


def test_server_logger_records_events(tmp_path):
    log_dir = str(tmp_path / "logs")
    logger = ServerLogger(log_dir=log_dir)

    ev1 = GameStartedEvent(at_ms=0, rows=8, cols=8)
    logger.on_event(ev1)

    ev2 = ScoreUpdatedEvent(at_ms=500, white_score=3, black_score=0)
    logger.on_event(ev2)

    assert len(logger.log_entries) == 2
    assert logger.log_entries[0]["event_type"] == "GameStartedEvent"
    assert logger.log_entries[1]["event_type"] == "ScoreUpdatedEvent"


def test_client_log_ingestor(tmp_path):
    log_dir = str(tmp_path / "client_logs")
    ingestor = ClientLogIngestor(log_dir=log_dir)

    entries = [
        {"timestamp": 123456, "action": "click", "target": "e2"},
        {"timestamp": 123457, "action": "click", "target": "e4"},
    ]

    count = ingestor.ingest_client_logs("player1", entries)
    assert count == 2

    # Check file exists and is populated
    file_path = os.path.join(log_dir, "client_player1.jsonl")
    assert os.path.exists(file_path)
