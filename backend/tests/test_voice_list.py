from __future__ import annotations

import json
from urllib.error import HTTPError

import pytest

from voice_list import app as voice_app


def test_handle_event_filters_remote_results(monkeypatch):
    def fake_fetch(self):
        return [
            {
                "voice_id": "alpha",
                "name": "Alpha",
                "labels": {"quality": "highest", "language": "english", "use_case": "narrative & story"},
            },
            {
                "voice_id": "beta",
                "name": "Beta",
                "labels": {"quality": "standard", "language": "english", "use_case": "social media"},
            },
        ]

    monkeypatch.setattr(voice_app.VoiceListApplication, "_fetch_voices", fake_fetch)
    monkeypatch.setattr(voice_app, "TARGET_QUALITIES", set())
    application = voice_app.VoiceListApplication()

    response = application.handle_event({"httpMethod": "GET"})
    assert response["statusCode"] == 200
    payload = json.loads(response["body"])
    assert [voice["voiceId"] for voice in payload["voices"]] == ["alpha", "beta"]


def test_handle_event_returns_top_results_when_filtered_under_min(monkeypatch):
    def fake_fetch(self):
        return [
            {
                "voice_id": "alpha",
                "name": "Alpha",
                "labels": {"quality": "highest", "language": "english"},
            },
            {
                "voice_id": "beta",
                "name": "Beta",
                "labels": {"quality": "standard", "language": "english"},
            },
        ]

    monkeypatch.setattr(voice_app.VoiceListApplication, "_fetch_voices", fake_fetch)
    monkeypatch.setattr(voice_app, "TARGET_QUALITIES", {"highest"})
    monkeypatch.setattr(voice_app, "MIN_RESULTS", 2)
    application = voice_app.VoiceListApplication()

    response = application.handle_event({"httpMethod": "GET"})
    assert response["statusCode"] == 200
    payload = json.loads(response["body"])
    assert [voice["voiceId"] for voice in payload["voices"]] == ["alpha", "beta"]


def test_handle_event_uses_fallback_on_permission_error(monkeypatch):
    def unauthorized(self):
        raise HTTPError(voice_app.VOICE_API_URL, 401, "Unauthorized", hdrs=None, fp=None)

    monkeypatch.setattr(voice_app.VoiceListApplication, "_fetch_voices", unauthorized)
    sentinel = [{"voiceId": "fallback", "name": "Fallback"}]
    monkeypatch.setattr(voice_app.VoiceListApplication, "_load_fallback_voices", lambda self: sentinel)

    application = voice_app.VoiceListApplication()
    response = application.handle_event({"httpMethod": "GET"})
    assert response["statusCode"] == 200
    payload = json.loads(response["body"])
    assert payload["voices"] == sentinel


def test_handle_event_falls_back_when_filters_empty(monkeypatch):
    monkeypatch.setattr(voice_app.VoiceListApplication, "_fetch_voices", lambda self: [])
    sentinel = [{"voiceId": "fallback2", "name": "Fallback 2"}]
    monkeypatch.setattr(voice_app.VoiceListApplication, "_load_fallback_voices", lambda self: sentinel)

    application = voice_app.VoiceListApplication()
    response = application.handle_event({"httpMethod": "GET"})
    data = json.loads(response["body"])
    assert data["voices"] == sentinel
