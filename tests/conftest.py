"""Shared fixtures."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

from app.ingest.pipeline import Corpus, load_corpus

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _corpus_available() -> bool:
    return all((DATA_DIR / "raw" / f"sample_{i}.pdf").exists() for i in range(1, 7))


@pytest.fixture(scope="session")
def corpus() -> Corpus:
    """The real six-PDF corpus (parsed once per session)."""
    if not _corpus_available():
        pytest.skip("source PDFs not present in data/raw")
    return load_corpus(DATA_DIR)


_LOOPBACK = ("127.0.0.1", "::1", "localhost")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never reach a real service (e.g. api.openai.com). Loopback stays allowed.

    DNS resolution is blocked as well as `connect`: on Windows the asyncio Proactor loop connects via
    ConnectEx and bypasses `socket.connect`, but every connection resolves its host first.
    """
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOOPBACK:
            raise RuntimeError(f"network access blocked in tests: {address!r}")
        return real_connect(self, address)

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host not in _LOOPBACK and host not in (None, b"localhost"):
            raise RuntimeError(f"network access blocked in tests: {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
