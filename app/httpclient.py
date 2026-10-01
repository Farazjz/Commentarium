"""Shared HTTP helpers.

Root cause this fixes: on this machine there is a system proxy registered in
the Windows registry (`127.0.0.1:3067`). Python's `urllib` (used by httpx when
`trust_env=True`) discovers it via the registry but does NOT honor the
Windows "ProxyOverride / bypass for <local>" list. As a result, httpx sends
even `localhost`/`127.0.0.1` requests through that proxy, which rewrites them
to an HTTPS/Cloudflare edge -> the classic
"400 The plain HTTP request was sent to HTTPS port" error (and other 502/401s).

`curl` works because it DOES honor the localhost bypass list.

To keep the app reliable regardless of the machine's proxy settings we make
HTTP calls proxy-less (direct). Real, legitimate proxies can still be set via
the HTTP_PROXY / HTTPS_PROXY / ALL_PROXY environment variables, which the
users/sysadmins choose to enable explicitly.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

"""Environment variable that, when set to a truthy value, makes the app
honor HTTP(S)_PROXY environment variables (advanced use)."""
_ENV_PROXY_FLAG = "APP_USE_ENV_PROXY"


def _use_env_proxy() -> bool:
    return os.environ.get(_ENV_PROXY_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def get_client(
    *,
    timeout: float | httpx.Timeout = 60.0,
    verify: bool = True,
    **kwargs: Any,
) -> httpx.Client:
    """Build an httpx.Client that by default bypasses the system proxy.

    Pass `trust_env=True` explicitly (along with APP_USE_ENV_PROXY=1) if you
    want to route through HTTP(S)_PROXY environment variables.
    """
    kwargs.setdefault("timeout", timeout)
    kwargs.setdefault("verify", verify)
    if _use_env_proxy():
        kwargs.setdefault("trust_env", True)  # honor HTTP(S)_PROXY env vars
    else:
        kwargs["trust_env"] = False  # ignore Windows registry proxy
    return httpx.Client(**kwargs)


def get(
    url: str,
    *,
    timeout: float = 60.0,
    **kwargs: Any,
) -> httpx.Response:
    return get_client(timeout=timeout).request("GET", url, **kwargs)


def post(
    url: str,
    *,
    timeout: float = 60.0,
    **kwargs: Any,
) -> httpx.Response:
    return get_client(timeout=timeout).request("POST", url, **kwargs)
