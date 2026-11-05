"""The development nginx (docker/nginx.conf, baked into the frontend image) and the server nginx
(docker/nginx.strict.conf, mounted by docker-compose.server.yml) are two files with the same intent. These tests
catch a security header added to, changed in or dropped from one of them but not the other."""
import re
from pathlib import Path

import pytest

DOCKER = Path(__file__).resolve().parents[2] / "docker"
DEVELOPMENT, SERVER = DOCKER / "nginx.conf", DOCKER / "nginx.strict.conf"
# Sent only by the server nginx: it sits behind an HTTPS reverse proxy, the development one serves plain HTTP.
HTTPS_ONLY = {"Strict-Transport-Security"}
HEADER = re.compile(r'^\s*add_header\s+(\S+)\s+"([^"]*)"\s+always;', re.MULTILINE)


def _headers(path: Path) -> dict[str, str]:
    return dict(HEADER.findall(path.read_text()))


def _location_blocks(path: Path) -> list[str]:
    """The text of every location block (they hold no nested blocks in these files)."""
    return re.findall(r"location\s[^{]*\{([^}]*)\}", path.read_text())


def test_both_send_the_same_security_headers():
    development, server = _headers(DEVELOPMENT), _headers(SERVER)

    assert development, "no add_header lines found; did the file format change?"
    assert {name: value for name, value in server.items() if name not in HTTPS_ONLY} == development


def test_only_the_server_nginx_sends_hsts():
    assert HTTPS_ONLY <= set(_headers(SERVER))
    assert not HTTPS_ONLY & set(_headers(DEVELOPMENT))


@pytest.mark.parametrize("path", [DEVELOPMENT, SERVER], ids=lambda path: path.name)
def test_no_location_drops_the_server_wide_headers(path):
    """An add_header inside a location replaces every add_header from the server block for that location."""
    blocks = _location_blocks(path)

    assert blocks and not any("add_header" in block for block in blocks)
