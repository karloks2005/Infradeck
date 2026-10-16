"""Sending alerts to the places a user chose: ntfy, a Discord webhook, any other webhook, or email.

The address of a channel is something a signed-in user typed, and the backend then connects to it, so it is treated
as hostile: only http and https, no credentials in the address, no redirects, a short timeout, a small answer that
is never shown to the user (only its status), and every address the name resolves to must be one a channel may
reach. The connection then goes to the address that was checked, not to the name, so a DNS answer that changes
between the check and the connection (DNS rebinding) can't lead it anywhere else. Loopback, link-local (which includes cloud metadata) and other special ranges are always refused; private
LAN ranges are allowed only for admins, because a homelab's ntfy usually lives there.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import os
import smtplib
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WEB_KINDS = ("ntfy", "discord", "webhook")
KINDS = (*WEB_KINDS, "email")
TIMEOUT_SECONDS = 5
MAX_URL = 500
MAX_CHANNELS_PER_USER = 8
EVENTS = ("critical", "warning", "resolved")
DEFAULT_ROUTING = {"critical": True, "warning": True, "resolved": False}


class ChannelError(ValueError):
    """The address can't be used; the message is safe to show."""


def _allowed(address: str, allow_private: bool) -> bool:
    ip = ipaddress.ip_address(address)
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return False
    return allow_private or ip.is_global


def resolve(host: str, port: int) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except (socket.gaierror, UnicodeError) as error:
        raise ChannelError("That address does not resolve.") from error


def check_url(url: str, allow_private: bool) -> tuple[str, str]:
    """The cleaned address and a short form safe to display, or ChannelError."""
    url, display, _address = _checked(url, allow_private)
    return url, display


def _checked(url: str, allow_private: bool) -> tuple[str, str, str]:
    """check_url, plus the checked IP address that a send must connect to."""
    url = (url or "").strip()
    if not url or len(url) > MAX_URL or not url.isprintable():
        raise ChannelError("Enter the channel's address.")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ChannelError("The address must start with http:// or https://.")
    if parts.username or parts.password:
        raise ChannelError("Put no user name or password in the address.")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as error:
        raise ChannelError("The port in that address is not valid.") from error
    addresses = resolve(parts.hostname, port)
    if not addresses or not all(_allowed(address, allow_private) for address in addresses):
        raise ChannelError("That address points at this machine or a private network." + ("" if allow_private else " Only an admin can add a channel on the local network."))
    tail = parts.path.rstrip("/").rsplit("/", 1)[-1]
    return url, f"{parts.hostname}{'/…' + tail[-6:] if tail else ''}"[:120], addresses[0]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # a redirect could lead anywhere, including inside
        return None


def _pinned(connection_class, address: str):
    """A connection class that connects to ``address`` whatever the URL's host name resolves to now. The name is
    still what goes in the Host header and, for https, in SNI and the certificate check."""
    class Pinned(connection_class):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._create_connection = lambda target, *rest, **options: socket.create_connection((address, target[1]), *rest, **options)
    return Pinned


class _PinnedHTTP(urllib.request.HTTPHandler):
    def __init__(self, address: str):
        super().__init__()
        self.connection = _pinned(http.client.HTTPConnection, address)

    def http_open(self, req):
        return self.do_open(self.connection, req)


class _PinnedHTTPS(urllib.request.HTTPSHandler):
    def __init__(self, address: str):
        super().__init__()
        self.connection = _pinned(http.client.HTTPSConnection, address)

    def https_open(self, req):
        return self.do_open(self.connection, req, context=self._context)


def _post(url: str, data: bytes, headers: dict[str, str], address: str) -> tuple[bool, str]:
    """POST to ``url``, connecting to the already checked ``address``. No proxy: one would resolve the name itself."""
    request = urllib.request.Request(url, data=data, headers={"User-Agent": "Infradeck", **headers}, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect, _PinnedHTTP(address), _PinnedHTTPS(address))
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            response.read(1024)
            return True, f"{response.status}"
    except urllib.error.HTTPError as error:
        return False, f"{error.code}"
    except (urllib.error.URLError, OSError, ValueError) as error:
        return False, type(getattr(error, "reason", error)).__name__[:40]


def smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST", "").strip() and os.getenv("SMTP_FROM", "").strip())


def _send_email(address: str, title: str, body: str) -> tuple[bool, str]:
    if not smtp_configured():
        return False, "SMTP is not configured"
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = os.environ["SMTP_FROM"], address, title[:200]
    message.set_content(body)
    try:
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587")), timeout=TIMEOUT_SECONDS * 2) as server:
            if os.getenv("SMTP_STARTTLS", "true").lower() != "false":
                server.starttls()
            if os.getenv("SMTP_USER"):
                server.login(os.environ["SMTP_USER"], os.getenv("SMTP_PASSWORD", ""))
            server.send_message(message)
        return True, "sent"
    except (smtplib.SMTPException, OSError, ValueError) as error:
        return False, type(error).__name__[:40]


def send(kind: str, target: str, title: str, body: str, severity: str = "INFO", allow_private: bool = False) -> tuple[bool, str]:
    """Deliver one message. Returns (worked, a short status). The address is checked again at send time, because
    what a name resolves to can change after the channel was saved."""
    if kind == "email":
        return _send_email(target, title, body)
    try:
        url, _display, address = _checked(target, allow_private)
    except ChannelError:
        return False, "address refused"
    if kind == "ntfy":
        # Header values must be Latin-1; anything else in a title goes into the body instead.
        safe = title.encode("latin-1", "ignore").decode("latin-1")[:200]
        return _post(url, body.encode(), {"Title": safe, "Priority": "5" if severity == "CRITICAL" else "3", "Tags": "rotating_light" if severity == "CRITICAL" else "warning" if severity == "WARNING" else "white_check_mark"}, address)
    if kind == "discord":
        return _post(url, json.dumps({"content": f"**{title}**\n{body}"[:1900], "allowed_mentions": {"parse": []}}).encode(), {"Content-Type": "application/json"}, address)
    return _post(url, json.dumps({"title": title, "body": body, "severity": severity}).encode(), {"Content-Type": "application/json"}, address)


def in_quiet_hours(quiet: dict | None, timezone_name: str | None, now: datetime) -> bool:
    """Whether ``now`` falls in the user's quiet hours ("23:00" to "07:00" may wrap past midnight)."""
    if not quiet or not quiet.get("enabled"):
        return False
    try:
        local = now.astimezone(ZoneInfo(timezone_name or "UTC"))
        start, end = (int(part[:2]) * 60 + int(part[3:5]) for part in (quiet["start"], quiet["end"]))
    except (ZoneInfoNotFoundError, KeyError, ValueError, TypeError):
        return False
    minute = local.hour * 60 + local.minute
    return start <= minute < end if start < end else minute >= start or minute < end if start != end else False
