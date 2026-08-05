from __future__ import annotations

import json

from fastapi.testclient import TestClient

from conftest import FakeClient
from satemshi.line_bot.app import create_app
from satemshi.line_bot.signature import sign, verify


def post(client: TestClient, config, payload: dict, secret: str | None = None):
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers["X-Line-Signature"] = sign(secret, body)
    return client.post(config.line_bot.webhook_path, content=body, headers=headers)


def test_sign_and_verify_round_trip():
    body = b'{"events":[]}'
    assert verify("s3cret", body, sign("s3cret", body)) is True
    assert verify("s3cret", body, sign("other", body)) is False
    assert verify("s3cret", body, None) is False
    assert verify("", body, sign("", body)) is False


def test_webhook_rejects_a_bad_signature(make_config):
    config = make_config()
    fake = FakeClient()
    with TestClient(create_app(config, fake)) as client:
        response = post(client, config, {"events": []}, secret="wrong-secret")

    assert response.status_code == 401
    assert fake.replies == []


def test_webhook_rejects_an_unsigned_request(make_config):
    config = make_config()
    with TestClient(create_app(config, FakeClient())) as client:
        response = post(client, config, {"events": []})

    assert response.status_code == 401


def test_webhook_dispatches_a_signed_event(make_config, vault):
    config = make_config()
    fake = FakeClient()
    payload = {
        "events": [
            {
                "type": "message",
                "webhookEventId": "e1",
                "replyToken": "r1",
                "source": {"type": "user", "userId": "U1"},
                "message": {"type": "text", "id": "m1", "text": "note ran 5k"},
            }
        ]
    }
    with TestClient(create_app(config, fake)) as client:
        response = post(client, config, payload, secret="s3cret")

    assert response.status_code == 200
    assert "ran 5k" in fake.last_reply
    notes = list((vault / "Daily Notes").glob("*.md"))
    assert len(notes) == 1
    assert "ran 5k" in notes[0].read_text()


def test_one_failing_event_does_not_stop_the_others(make_config, monkeypatch):
    config = make_config()
    fake = FakeClient()
    app = create_app(config, fake)

    seen: list[str] = []
    original = app.state.bot.handle_event

    async def flaky(event):
        if event.get("webhookEventId") == "bad":
            raise RuntimeError("boom")
        seen.append(event["webhookEventId"])
        await original(event)

    monkeypatch.setattr(app.state.bot, "handle_event", flaky)
    payload = {
        "events": [
            {"type": "message", "webhookEventId": "bad"},
            {
                "type": "message",
                "webhookEventId": "good",
                "replyToken": "r",
                "source": {"userId": "U1"},
                "message": {"type": "text", "id": "m", "text": "note fine"},
            },
        ]
    }
    with TestClient(app) as client:
        response = post(client, config, payload, secret="s3cret")

    assert response.status_code == 200
    assert seen == ["good"]


def test_healthz(make_config):
    config = make_config()
    with TestClient(create_app(config, FakeClient())) as client:
        body = client.get("/healthz").json()

    assert body["status"] == "ok"
    assert body["vault_present"] is True
    assert body["webhook_path"] == "/line/webhook"
