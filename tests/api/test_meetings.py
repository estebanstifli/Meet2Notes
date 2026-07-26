from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_meeting_crud_and_permanent_delete(client: TestClient, data_dir: Path) -> None:
    created = client.post(
        "/api/meetings",
        json={
            "title": "  Product review  ",
            "description": "Decide the release scope.",
            "language": "en",
        },
    )
    assert created.status_code == 201
    meeting = created.json()
    assert meeting["title"] == "Product review"
    assert meeting["status"] == "draft"
    meeting_directory = data_dir / "meetings" / meeting["uuid"]
    assert (meeting_directory / "original").is_dir()
    assert (meeting_directory / "exports").is_dir()

    listed = client.get("/api/meetings", params={"search": "product"})
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [meeting["id"]]

    updated = client.patch(
        f"/api/meetings/{meeting['id']}",
        json={"title": "Release review", "description": None},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Release review"
    assert updated.json()["description"] is None

    deleted = client.delete(f"/api/meetings/{meeting['id']}")
    assert deleted.status_code == 204
    assert not meeting_directory.exists()
    assert client.get(f"/api/meetings/{meeting['id']}").status_code == 404


def test_meeting_validation_is_clear(client: TestClient) -> None:
    empty = client.post("/api/meetings", json={"title": " "})
    assert empty.status_code == 422
    assert empty.json()["detail"] == "A meeting title is required"

    unknown = client.patch("/api/meetings/12345", json={"title": "Still missing"})
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "Meeting not found"
