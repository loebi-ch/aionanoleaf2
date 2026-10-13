"""Pytest configuration and compatibility shims."""

from unittest.mock import MagicMock

import aiohttp.client_reqrep

# Compatibility shim: aioresponses does not yet pass 'stream_writer' required in aiohttp >= 3.14
_orig_client_response_init = aiohttp.client_reqrep.ClientResponse.__init__


def _patched_client_response_init(self, *args, stream_writer=None, **kwargs):
    if stream_writer is None:
        stream_writer = MagicMock()
    return _orig_client_response_init(
        self, *args, stream_writer=stream_writer, **kwargs
    )


aiohttp.client_reqrep.ClientResponse.__init__ = _patched_client_response_init
