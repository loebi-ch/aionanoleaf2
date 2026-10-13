"""Unit tests for the aionanoleaf2 client: host handling, status mapping, retries."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any, Final

import pytest
import pytest_asyncio
from aiohttp import (
    ClientConnectionError,
    ClientPayloadError,
    ClientSession,
    ServerTimeoutError,
)
from aioresponses import aioresponses

from aionanoleaf2 import (
    AuthError,
    EndpointNotFoundError,
    InvalidResponseError,
    InvalidToken,
    Nanoleaf,
    NanoleafConnectionError,
    NanoleafError,
    NanoleafTimeoutError,
    NoAuthToken,
    Unauthorized,
    Unavailable,
)

HOST: Final = "192.168.0.10"
PORT: Final = 16021
TOKEN: Final = "TESTTOKEN0123456789"
NEW_TOKEN: Final = "NEWTOKEN9876543210"
BASE_URL: Final = f"http://{HOST}:{PORT}/api/v1"
AUTH_URL: Final = f"{BASE_URL}/new"

STATE: Final[dict[str, Any]] = {
    "on": {"value": True},
    "brightness": {"value": 40, "max": 100, "min": 0},
    "ct": {"value": 2700, "max": 6500, "min": 1200},
    "colorMode": "ct",
}

ESSENTIALS_ROOT: Final[dict[str, Any]] = {
    "name": "Essentials A19",
    "serialNo": "S12345",
    "manufacturer": "Nanoleaf",
    "firmwareVersion": "3.5.10",
    "model": "NL72K3",
}

CANVAS_ROOT: Final[dict[str, Any]] = {
    "name": "Canvas 1234",
    "serialNo": "S999",
    "manufacturer": "Nanoleaf",
    "firmwareVersion": "3.5.10",
    "hardwareVersion": "1.2",
    "model": "NL29",
    "state": {
        "on": {"value": True},
        "brightness": {"value": 80, "max": 100, "min": 0},
        "hue": {"value": 120, "max": 360, "min": 0},
        "sat": {"value": 50, "max": 100, "min": 0},
        "ct": {"value": 2700, "max": 6500, "min": 1200},
        "colorMode": "hs",
    },
    "effects": {"select": "Rainbow", "effectsList": ["Rainbow", "Sunset"]},
    "panelLayout": {
        "layout": {
            "numPanels": 1,
            "sideLength": 100,
            "positionData": [{"panelId": 1, "x": 0, "y": 0, "o": 0, "shapeType": 2}],
        },
        "globalOrientation": {"value": 0, "max": 360, "min": 0},
    },
}


# --- fixtures & helpers ------------------------------------------------------


@pytest_asyncio.fixture
async def session() -> AsyncIterator[ClientSession]:
    async with ClientSession() as client_session:
        yield client_session


@pytest.fixture
def mocked() -> Iterator[aioresponses]:
    with aioresponses() as mock:
        yield mock


@pytest.fixture
def client(session: ClientSession) -> Nanoleaf:
    return Nanoleaf(session, HOST, TOKEN)


def api(path: str = "") -> str:
    """Return the authorized API URL for a path."""
    return f"{BASE_URL}/{TOKEN}/{path}"


def _total_calls(mock: aioresponses) -> int:
    return sum(len(calls) for calls in mock.requests.values())


def _calls_to(mock: aioresponses, suffix: str) -> int:
    return sum(
        len(calls)
        for (_, url), calls in mock.requests.items()
        if str(url).endswith(suffix)
    )


# --- _format_host / host handling -------------------------------------------

FORMAT_HOST_CASES: Final[list[tuple[str, str]]] = [
    ("fe80::1%eth0", "[fe80::1%25eth0]"),
    ("[fe80::1%25eth0]", "[fe80::1%25eth0]"),
    ("fe80::1%25", "[fe80::1%2525]"),
    ("[fe80::1%2525]", "[fe80::1%2525]"),
    ("2001:db8::1", "[2001:db8::1]"),
    ("[2001:db8::1]", "[2001:db8::1]"),
    ("192.168.0.10", "192.168.0.10"),
    ("nl.local", "nl.local"),
]


@pytest.mark.parametrize(("raw", "expected"), FORMAT_HOST_CASES)
def test_format_host(raw: str, expected: str) -> None:
    assert Nanoleaf._format_host(raw) == expected


@pytest.mark.parametrize("raw", [case[0] for case in FORMAT_HOST_CASES])
def test_format_host_is_idempotent(raw: str) -> None:
    once = Nanoleaf._format_host(raw)
    for _ in range(3):
        assert Nanoleaf._format_host(once) == once


@pytest.mark.parametrize(
    ("host", "expected_raw_host"),
    [
        ("fe80::1%eth0", "fe80::1"),
        ("[fe80::1%25eth0]", "fe80::1"),
        ("2001:db8::1", "2001:db8::1"),
        ("192.168.0.10", "192.168.0.10"),
    ],
)
async def test_raw_host(
    session: ClientSession, host: str, expected_raw_host: str
) -> None:
    assert Nanoleaf(session, host).raw_host == expected_raw_host


async def test_stored_host_roundtrip_is_stable(session: ClientSession) -> None:
    """HA stores nanoleaf.host and feeds it back into the constructor."""
    first = Nanoleaf(session, "fe80::1%eth0")
    second = Nanoleaf(session, first.host)
    assert first.host == second.host == "[fe80::1%25eth0]"
    assert first.raw_host == second.raw_host == "fe80::1"
    assert second._api_url == f"http://[fe80::1%25eth0]:{PORT}/api/v1"


# --- _request: tokens, success paths ----------------------------------------


@pytest.mark.parametrize("token", [None, ""])
async def test_request_without_token_raises_before_any_http_call(
    session: ClientSession, mocked: aioresponses, token: str | None
) -> None:
    unauthenticated = Nanoleaf(session, HOST, token)
    with pytest.raises(NoAuthToken):
        await unauthenticated._request("get", "state")
    assert _total_calls(mocked) == 0


@pytest.mark.parametrize("token", [None, ""])
async def test_auth_token_property_raises_without_token(
    session: ClientSession,
    token: str | None,
) -> None:
    with pytest.raises(NoAuthToken):
        _ = Nanoleaf(session, HOST, token).auth_token


async def test_request_returns_decoded_json(
    client: Nanoleaf, mocked: aioresponses
) -> None:
    mocked.get(api("state"), payload={"on": {"value": True}})
    assert await client._request("get", "state") == {"on": {"value": True}}


async def test_request_accepts_json_suffix_content_type(
    client: Nanoleaf, mocked: aioresponses
) -> None:
    mocked.get(
        api("state"),
        body='{"a": 1}',
        content_type="application/vnd.nanoleaf+json",
    )
    assert await client._request("get", "state") == {"a": 1}


@pytest.mark.parametrize(
    ("status", "body"),
    [
        pytest.param(204, "", id="204-no-content"),
        pytest.param(200, "", id="200-empty-body"),
    ],
)
async def test_request_empty_response_returns_none(
    client: Nanoleaf, mocked: aioresponses, status: int, body: str
) -> None:
    mocked.put(api("state"), status=status, body=body)
    assert await client._request("put", "state", {"on": {"value": True}}) is None


async def test_request_sends_json_body(client: Nanoleaf, mocked: aioresponses) -> None:
    payload = {"on": {"value": True}}
    mocked.put(api("state"), status=204)
    await client._request("put", "state", payload)
    (calls,) = mocked.requests.values()
    assert calls[0].kwargs["json"] == payload


# --- _request: status code matrix -------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        pytest.param(401, InvalidToken, id="401-invalid-token"),
        pytest.param(404, EndpointNotFoundError, id="404-endpoint-not-found"),
        pytest.param(422, Unavailable, id="422"),
        pytest.param(500, Unavailable, id="500"),
        pytest.param(502, Unavailable, id="502"),
        pytest.param(503, Unavailable, id="503"),
    ],
)
async def test_request_status_mapping_and_no_retry(
    client: Nanoleaf,
    mocked: aioresponses,
    status: int,
    expected: type[NanoleafError],
) -> None:
    mocked.get(api("state"), status=status, repeat=True)
    with pytest.raises(NanoleafError) as excinfo:
        await client._request("get", "state")
    assert type(excinfo.value) is expected  # exact type, not a transport subclass
    assert _total_calls(mocked) == 1  # HTTP errors are never retried


async def test_request_404_exposes_path_and_is_not_unavailable(
    client: Nanoleaf, mocked: aioresponses
) -> None:
    mocked.get(api("effects/select"), status=404)
    with pytest.raises(EndpointNotFoundError) as excinfo:
        await client._request("get", "effects/select")
    assert excinfo.value.path == "effects/select"
    assert not isinstance(excinfo.value, Unavailable)


@pytest.mark.parametrize("content_type", ["text/html", "text/plain"])
async def test_request_unexpected_content_type(
    client: Nanoleaf, mocked: aioresponses, content_type: str
) -> None:
    mocked.get(
        api("effects"),
        status=200,
        body="<html>oops</html>",
        content_type=content_type,
    )
    with pytest.raises(InvalidResponseError):
        await client._request("get", "effects")
    assert _total_calls(mocked) == 1


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("{not json", id="garbage"),
        pytest.param('{"unterminated": ', id="truncated"),
        pytest.param(b"\xff\xfe\x00", id="binary"),
    ],
)
async def test_request_malformed_json(
    client: Nanoleaf, mocked: aioresponses, body: str | bytes
) -> None:
    mocked.get(api("state"), status=200, body=body, content_type="application/json")
    with pytest.raises(InvalidResponseError):
        await client._request("get", "state")
    assert _total_calls(mocked) == 1


# --- _request: transport errors & retries -----------------------------------


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        pytest.param(TimeoutError(), NanoleafTimeoutError, id="timeout"),
        pytest.param(
            ServerTimeoutError("timed out"),
            NanoleafTimeoutError,
            id="server-timeout-is-timeout-not-connection",
        ),
        pytest.param(
            ClientConnectionError("down"),
            NanoleafConnectionError,
            id="connection-error",
        ),
    ],
)
async def test_request_transport_error_after_all_retries(
    client: Nanoleaf,
    mocked: aioresponses,
    raised: Exception,
    expected: type[Unavailable],
) -> None:
    mocked.get(api("state"), exception=raised, repeat=True)
    with pytest.raises(NanoleafError) as excinfo:
        await client._request("get", "state")
    assert type(excinfo.value) is expected
    assert isinstance(excinfo.value, Unavailable)  # backwards compatible
    assert isinstance(excinfo.value.__cause__, type(raised))
    assert _total_calls(mocked) == 3  # default retries


@pytest.mark.parametrize("retries", [1, 2, 3, 5])
async def test_retry_count_matches_configuration(
    session: ClientSession, mocked: aioresponses, retries: int
) -> None:
    configured = Nanoleaf(session, HOST, TOKEN, retries=retries)
    mocked.get(api("state"), exception=ClientConnectionError("down"), repeat=True)
    with pytest.raises(NanoleafConnectionError):
        await configured._request("get", "state")
    assert _total_calls(mocked) == retries


async def test_zero_retries_still_performs_one_attempt(
    session: ClientSession, mocked: aioresponses
) -> None:
    configured = Nanoleaf(session, HOST, TOKEN, retries=0)
    mocked.get(api("state"), exception=TimeoutError(), repeat=True)
    with pytest.raises(NanoleafTimeoutError):
        await configured._request("get", "state")
    assert _total_calls(mocked) == 1


async def test_request_recovers_after_transient_error(
    client: Nanoleaf, mocked: aioresponses
) -> None:
    mocked.get(api("state"), exception=ClientConnectionError("blip"))
    mocked.get(api("state"), payload={"ok": True})
    assert await client._request("get", "state") == {"ok": True}
    assert _total_calls(mocked) == 2


async def test_client_payload_error_is_not_retried(
    client: Nanoleaf, mocked: aioresponses
) -> None:
    mocked.get(api("state"), exception=ClientPayloadError("truncated"), repeat=True)
    with pytest.raises(NanoleafConnectionError):
        await client._request("get", "state")
    assert _total_calls(mocked) == 1


@pytest.mark.parametrize(
    "mock_kwargs",
    [
        pytest.param({"status": 500}, id="500"),
        pytest.param({"status": 404}, id="404"),
        pytest.param({"exception": TimeoutError()}, id="timeout"),
        pytest.param({"exception": ClientConnectionError("down")}, id="connection"),
    ],
)
async def test_error_messages_do_not_leak_token(
    client: Nanoleaf, mocked: aioresponses, mock_kwargs: dict[str, Any]
) -> None:
    mocked.get(api("state"), repeat=True, **mock_kwargs)
    with pytest.raises(NanoleafError) as excinfo:
        await client._request("get", "state")
    assert TOKEN not in str(excinfo.value)


# --- authorize ---------------------------------------------------------------


async def test_authorize_stores_token(
    session: ClientSession, mocked: aioresponses
) -> None:
    unauthenticated = Nanoleaf(session, HOST)
    mocked.post(AUTH_URL, payload={"auth_token": NEW_TOKEN})
    await unauthenticated.authorize()
    assert unauthenticated.auth_token == NEW_TOKEN


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        pytest.param(403, Unauthorized, id="403-pairing-mode-inactive"),
        pytest.param(404, Unavailable, id="404-is-not-endpoint-not-found"),
        pytest.param(500, Unavailable, id="500"),
        pytest.param(502, Unavailable, id="502"),
    ],
)
async def test_authorize_status_mapping(
    session: ClientSession,
    mocked: aioresponses,
    status: int,
    expected: type[NanoleafError],
) -> None:
    unauthenticated = Nanoleaf(session, HOST)
    mocked.post(AUTH_URL, status=status, repeat=True)
    with pytest.raises(NanoleafError) as excinfo:
        await unauthenticated.authorize()
    assert type(excinfo.value) is expected
    assert _total_calls(mocked) == 1
    with pytest.raises(NoAuthToken):  # no token stored on failure
        _ = unauthenticated.auth_token


async def test_authorize_403_is_not_an_auth_error(
    session: ClientSession, mocked: aioresponses
) -> None:
    mocked.post(AUTH_URL, status=403)
    with pytest.raises(Unauthorized) as excinfo:
        await Nanoleaf(session, HOST).authorize()
    assert not isinstance(excinfo.value, AuthError)  # must not trigger reauth


@pytest.mark.parametrize(
    "mock_kwargs",
    [
        pytest.param({"payload": {"foo": "bar"}}, id="missing-token-key"),
        pytest.param({"payload": {"auth_token": ""}}, id="empty-token"),
        pytest.param({"payload": ["not", "an", "object"]}, id="not-an-object"),
        pytest.param(
            {"body": "<html/>", "content_type": "text/html"}, id="html-content-type"
        ),
        pytest.param(
            {"body": "{broken", "content_type": "application/json"}, id="bad-json"
        ),
    ],
)
async def test_authorize_invalid_response(
    session: ClientSession, mocked: aioresponses, mock_kwargs: dict[str, Any]
) -> None:
    unauthenticated = Nanoleaf(session, HOST)
    mocked.post(AUTH_URL, status=200, **mock_kwargs)
    with pytest.raises(InvalidResponseError):
        await unauthenticated.authorize()
    with pytest.raises(NoAuthToken):
        _ = unauthenticated.auth_token


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        pytest.param(TimeoutError(), NanoleafTimeoutError, id="timeout"),
        pytest.param(
            ClientConnectionError("down"), NanoleafConnectionError, id="connection"
        ),
    ],
)
async def test_authorize_transport_errors(
    session: ClientSession,
    mocked: aioresponses,
    raised: Exception,
    expected: type[Unavailable],
) -> None:
    mocked.post(AUTH_URL, exception=raised, repeat=True)
    with pytest.raises(NanoleafError) as excinfo:
        await Nanoleaf(session, HOST).authorize()
    assert type(excinfo.value) is expected
    assert _total_calls(mocked) == 1  # authorize does not retry


# --- get_info: optional endpoints vs. transport errors ----------------------


async def test_get_info_complete_root_payload_needs_single_request(
    client: Nanoleaf, mocked: aioresponses
) -> None:
    mocked.get(api(""), payload=CANVAS_ROOT)
    await client.get_info()

    assert client.name == "Canvas 1234"
    assert client.serial_no == "S999"
    assert client.is_on is True
    assert client.brightness == 80
    assert client.hue == 120
    assert client.saturation == 50
    assert client.color_temperature == 2700
    assert client.color_temperature_min == 1200
    assert client.color_temperature_max == 6500
    assert client.color_mode == "hs"
    assert client.effects_list == ["Rainbow", "Sunset"]
    assert client.effect == "Rainbow"
    assert len(client.panels) == 1
    assert client.supports_events is True
    assert _total_calls(mocked) == 1


async def test_get_info_essentials_fetches_optional_endpoints(
    client: Nanoleaf, mocked: aioresponses
) -> None:
    mocked.get(api(""), payload=ESSENTIALS_ROOT)
    mocked.get(api("state"), payload=STATE)
    mocked.get(api("effects/effectsList"), payload=["Aurora", "Sunrise"])
    mocked.get(api("effects/select"), payload="Aurora")

    await client.get_info()

    assert client.is_on is True
    assert client.brightness == 40
    assert client.color_temperature_max == 6500
    assert client.effects_list == ["Aurora", "Sunrise"]
    assert client.effect == "Aurora"
    assert client.panels == set()
    assert client.supports_events is False


async def test_get_info_tolerates_404_on_optional_endpoints(
    client: Nanoleaf, mocked: aioresponses
) -> None:
    mocked.get(api(""), payload=ESSENTIALS_ROOT)
    for path in ("state", "effects/effectsList", "effects/select"):
        mocked.get(api(path), status=404)

    await client.get_info()  # must not raise

    assert client.name == "Essentials A19"
    assert client.is_on is False
    assert client.effects_list == []
    assert client.effect == ""
    assert client.panels == set()
    assert client.supports_events is False
    assert _calls_to(mocked, "/state") == 1  # 404 is not retried
    assert _calls_to(mocked, "/effects/effectsList") == 1
    assert _calls_to(mocked, "/effects/select") == 1


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        pytest.param(TimeoutError(), NanoleafTimeoutError, id="timeout"),
        pytest.param(
            ClientConnectionError("down"), NanoleafConnectionError, id="connection"
        ),
    ],
)
@pytest.mark.parametrize(
    ("root", "failing_path"),
    [
        pytest.param(ESSENTIALS_ROOT, "state", id="state"),
        pytest.param(
            {**ESSENTIALS_ROOT, "state": STATE},
            "effects/effectsList",
            id="effects-list",
        ),
        pytest.param(
            {
                **ESSENTIALS_ROOT,
                "state": STATE,
                "effects": {"effectsList": ["Aurora"]},
            },
            "effects/select",
            id="selected-effect",
        ),
    ],
)
async def test_get_info_propagates_transport_errors(
    client: Nanoleaf,
    mocked: aioresponses,
    root: dict[str, Any],
    failing_path: str,
    raised: Exception,
    expected: type[Unavailable],
) -> None:
    client._is_on = True  # sentinel: a failed poll must not flip state to off
    mocked.get(api(""), payload=root)
    mocked.get(api(failing_path), exception=raised, repeat=True)

    with pytest.raises(expected):
        await client.get_info()

    assert _calls_to(mocked, f"/{failing_path}") == 3  # retried, then propagated
    assert client.is_on is True
