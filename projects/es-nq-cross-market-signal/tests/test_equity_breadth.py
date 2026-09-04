from datetime import datetime, timezone

from esnq_signal.equity_breadth import (
    EQUITY_BREADTH_SCHEMA_VERSION,
    EquityBreadthPoller,
    parse_equity_breadth_snapshot,
)


def test_equity_breadth_parser_accepts_v1_and_ignores_unknown_roots() -> None:
    ts = "2026-08-28T14:30:00+00:00"
    snapshot = parse_equity_breadth_snapshot(
        {
            "schema_version": EQUITY_BREADTH_SCHEMA_VERSION,
            "calculated_at": ts,
            "observations": {
                "AAPL": {
                    "root": "AAPL",
                    "observed_at": ts,
                    "price": 225.0,
                    "return_z": 1.2,
                    "imbalance_z": 0.8,
                    "composite_score": 1.0,
                    "vote": "bullish",
                    "issuer_id": "APPLE",
                    "roles": ["target"],
                    "health": {"healthy": True},
                },
                "UNKNOWN": {"root": "UNKNOWN", "observed_at": ts},
            },
            "health": {"healthy": False},
        }
    )
    assert snapshot is not None
    assert snapshot.schema_version == EQUITY_BREADTH_SCHEMA_VERSION
    assert set(snapshot.observations) == {"AAPL"}
    assert snapshot.observations["AAPL"].issuer_id == "APPLE"
    assert snapshot.observations["AAPL"].vote == "bullish"
    assert snapshot.healthy is False
    assert snapshot.signed_age_seconds(
        datetime(2026, 8, 28, 14, 30, 1, tzinfo=timezone.utc)
    ) == 1.0


def test_equity_breadth_parser_requires_timestamp_and_observation_mapping() -> None:
    assert parse_equity_breadth_snapshot(
        {"schema_version": EQUITY_BREADTH_SCHEMA_VERSION, "observations": {}}
    ) is None
    assert parse_equity_breadth_snapshot(
        {
            "schema_version": EQUITY_BREADTH_SCHEMA_VERSION,
            "calculated_at": "2026-08-28T14:30:00Z",
            "observations": [],
        }
    ) is None


def test_equity_breadth_parser_rejects_untyped_or_legacy_schema() -> None:
    base = {
        "calculated_at": "2026-08-28T14:30:00Z",
        "observations": {},
    }
    assert parse_equity_breadth_snapshot(base) is None
    assert parse_equity_breadth_snapshot(
        {**base, "schema_version": "equity_breadth_v1"}
    ) is None


def test_equity_breadth_parser_fails_observation_health_closed() -> None:
    ts = "2026-08-28T14:30:00Z"
    snapshot = parse_equity_breadth_snapshot(
        {
            "schema_version": EQUITY_BREADTH_SCHEMA_VERSION,
            "calculated_at": ts,
            "observations": {
                "QQQ": {
                    "root": "QQQ",
                    "observed_at": ts,
                    "return_z": 1.0,
                    "imbalance_z": 1.0,
                    "composite_score": 1.0,
                    "vote": "bullish",
                    "health": {"healthy": "false"},
                }
            },
            "health": {"status": "degraded"},
        }
    )
    assert snapshot is not None
    assert snapshot.healthy is False
    assert snapshot.observations["QQQ"].healthy is False


def _fetch_request_headers(monkeypatch, *, proxy_secret: str) -> dict[str, str]:
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def read(self) -> bytes:
            return (
                b'{"schema_version":"equity_breadth_snapshot_v1",'
                b'"calculated_at":"2026-08-28T14:30:00Z",'
                b'"observations":{},"health":{"healthy":true}}'
            )

    def fake_urlopen(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(
        "esnq_signal.equity_breadth.urllib.request.urlopen",
        fake_urlopen,
    )

    async def on_snapshot(_snapshot) -> None:
        return None

    poller = EquityBreadthPoller(
        url="http://127.0.0.1:8081/api/options/v1/equity-breadth",
        on_snapshot=on_snapshot,
        proxy_secret=proxy_secret,
        timeout_seconds=0.75,
    )
    assert poller._fetch() is not None
    assert captured["timeout"] == 0.75
    request = captured["request"]
    return {name.lower(): value for name, value in request.header_items()}


def test_equity_breadth_poller_sends_configured_proxy_secret_header(monkeypatch) -> None:
    headers = _fetch_request_headers(monkeypatch, proxy_secret="  shared-secret  ")
    assert headers["x-options-proxy-secret"] == "shared-secret"
    assert "authorization" not in headers


def test_equity_breadth_poller_omits_proxy_secret_header_when_blank(monkeypatch) -> None:
    headers = _fetch_request_headers(monkeypatch, proxy_secret="   ")
    assert "x-options-proxy-secret" not in headers
