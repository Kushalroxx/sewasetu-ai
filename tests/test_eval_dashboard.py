import json
import sys
from pathlib import Path

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard.eval_dashboard as dashboard


class FakeCursor:
    def __init__(self, status_counts):
        self.status_counts = status_counts
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query):
        self.queries.append(query)

    def fetchall(self):
        return self.status_counts


class FakeConnection:
    def __init__(self, status_counts):
        self.cursor_instance = FakeCursor(status_counts)
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def close(self):
        self.closed = True


def make_client(tmp_path, monkeypatch, status_counts):
    personas_file = tmp_path / "personas.json"
    personas_file.write_text(
        json.dumps(
            [
                {
                    "id": "synthetic-persona",
                    "name": "Synthetic Citizen",
                    "age": 70,
                    "village": "Example Village",
                    "block": "Sonari",
                    "how_they_talk": "Speaks simply.",
                    "opening_message": "Help me apply.",
                    "goal": "Apply for pension.",
                    "expected_portal_state": "One pending application.",
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard, "PERSONAS_FILE", personas_file)
    connection = FakeConnection(status_counts)
    app = Flask(__name__)
    app.register_blueprint(dashboard.eval_dashboard_bp)
    app.extensions["portal_db_connect"] = lambda: connection
    return app.test_client(), connection


def test_dashboard_displays_live_database_status_aggregates_only(
    tmp_path,
    monkeypatch,
):
    client, connection = make_client(
        tmp_path,
        monkeypatch,
        [
            ("PENDING", 4),
            ("APPROVED", 7),
            ("DEEMED_APPROVED", 2),
            ("REJECTED", 3),
            ("WITHDRAWN", 1),
        ],
    )

    response = client.get("/eval-dashboard")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "All applications" in page
    assert "Current application records" in page
    assert "Synthetic Citizen" in page
    assert "Scenario only" in page
    assert "assistant or MCP conversation was evaluated" in page
    assert "<span>All applications</span><strong>17</strong>" in page
    assert "<span>Deemed Approved</span><strong>2</strong>" in page
    assert connection.closed
    assert connection.cursor_instance.queries[0] == "SET TRANSACTION READ ONLY"


def test_dashboard_does_not_use_saved_json_results_as_evidence(
    tmp_path,
    monkeypatch,
):
    client, _ = make_client(tmp_path, monkeypatch, [("PENDING", 1)])

    response = client.get("/eval-dashboard")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "fake" not in page.lower()
    assert "no assistant conversation or persona-linked database check is stored" in page.lower()
    assert "Saved JSON evaluation results are not used as evidence" in page


def test_dashboard_shows_unavailable_when_database_is_not_configured(
    tmp_path,
    monkeypatch,
):
    client, _ = make_client(tmp_path, monkeypatch, [])
    app = client.application
    app.extensions.pop("portal_db_connect")

    page = client.get("/eval-dashboard").get_data(as_text=True)

    assert "Live portal counts are unavailable" in page
    assert "no counts are being estimated" in page
