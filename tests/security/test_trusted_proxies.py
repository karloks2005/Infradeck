"""docker/trusted-proxies.sh turns TRUSTED_PROXIES into the nginx settings that decide whose X-Forwarded-For is
believed. Nobody's by default; anything that isn't an IP address or CIDR range stops the container instead of
reaching the nginx configuration."""
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docker" / "trusted-proxies.sh"


def _run(tmp_path, value):
    out = tmp_path / "trusted-proxies.conf"
    env = {"PATH": os.environ["PATH"], **({} if value is None else {"TRUSTED_PROXIES": value})}
    result = subprocess.run(["sh", str(SCRIPT), str(out)], env=env, capture_output=True, text=True, timeout=10)
    return result, out.read_text() if out.exists() else None


def _directives(conf):
    return [line for line in conf.splitlines() if line and not line.startswith("#")]


@pytest.mark.parametrize("value", [None, "", " ", ",", " , "])
def test_by_default_nobody_is_trusted(tmp_path, value):
    result, conf = _run(tmp_path, value)

    assert result.returncode == 0 and _directives(conf) == []
    assert "nobody" in result.stdout


def test_each_proxy_is_trusted_and_the_header_read_from_it(tmp_path):
    result, conf = _run(tmp_path, "172.18.0.0/16, 10.8.0.1,fd00::/8,::1")

    assert result.returncode == 0
    assert _directives(conf) == ["set_real_ip_from 172.18.0.0/16;", "set_real_ip_from 10.8.0.1;", "set_real_ip_from fd00::/8;",
                                 "set_real_ip_from ::1;", "real_ip_header X-Forwarded-For;", "real_ip_recursive on;"]


@pytest.mark.parametrize("value", ["1.2.3.4;include /etc/passwd", "1.2.3.4;", "0.0.0.0/0;real_ip_header X-Real-IP", "*", "proxy.example.com",
                                   "traefik", "$(id)", "`id`", "1.2.3.4/16/8", "1.2.3.4\nset_real_ip_from 0.0.0.0/0", "10.0.0.1,evil;"])
def test_anything_but_addresses_stops_the_container(tmp_path, value):
    result, conf = _run(tmp_path, value)

    assert result.returncode != 0 and "is not an IP address or CIDR range" in result.stderr
    assert conf is None
