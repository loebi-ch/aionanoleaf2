"""Tests for the aionanoleaf2 exception hierarchy."""

from __future__ import annotations

import pytest

from aionanoleaf2 import (
    AuthError,
    EndpointNotFoundError,
    InvalidEffect,
    InvalidEmersion,
    InvalidResponseError,
    InvalidToken,
    NanoleafConnectionError,
    NanoleafError,
    NanoleafException,
    NanoleafTimeoutError,
    NoAuthToken,
    Unauthorized,
    Unavailable,
)


@pytest.mark.parametrize(
    ("child", "parent"),
    [
        (AuthError, NanoleafError),
        (InvalidToken, AuthError),
        (Unauthorized, NanoleafError),
        (NoAuthToken, NanoleafError),
        (Unavailable, NanoleafError),
        (NanoleafConnectionError, Unavailable),
        (NanoleafTimeoutError, Unavailable),
        (InvalidResponseError, NanoleafError),
        (EndpointNotFoundError, NanoleafError),
        (InvalidEffect, NanoleafError),
        (InvalidEffect, ValueError),
        (InvalidEmersion, NanoleafError),
        (InvalidEmersion, ValueError),
    ],
)
def test_is_subclass(child: type[Exception], parent: type[Exception]) -> None:
    assert issubclass(child, parent)


@pytest.mark.parametrize(
    ("child", "forbidden_parent"),
    [
        # Pairing-mode problem must never trigger a reauth flow.
        (Unauthorized, AuthError),
        (NoAuthToken, AuthError),
        # Reachable device without an optional endpoint is not "unavailable".
        (EndpointNotFoundError, Unavailable),
        (InvalidResponseError, Unavailable),
        (InvalidResponseError, AuthError),
        (AuthError, Unavailable),
        (Unavailable, AuthError),
    ],
)
def test_is_not_subclass(
    child: type[Exception], forbidden_parent: type[Exception]
) -> None:
    assert not issubclass(child, forbidden_parent)


def test_legacy_alias_is_same_class() -> None:
    assert NanoleafException is NanoleafError


def test_endpoint_not_found_carries_path() -> None:
    err = EndpointNotFoundError("effects/select")
    assert err.path == "effects/select"
    assert "effects/select" in str(err)
