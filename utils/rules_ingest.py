"""Input validation and safe URL fetching for game rulebook sources."""
import asyncio
import ipaddress
import logging
import socket
from email.message import Message
from urllib.parse import unquote, urljoin, urlparse

import aiohttp

from utils.rules_errors import RulesError

logger = logging.getLogger(__name__)

ALLOWED_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

_MB = 1048576
_REDIRECTS = (301, 302, 303, 307, 308)
_PUBLIC_ONLY = "❌ Only public http(s) links are allowed."


def validate_game_name(raw: str) -> str:
    name = " ".join((raw or "").split())
    if not name or len(name) > 100:
        raise RulesError("❌ Game names must be 1–100 characters.")
    return name


def validate_question(raw: str, max_chars: int) -> str:
    q = (raw or "").strip()
    if not q:
        raise RulesError("❌ Ask a question.")
    if len(q) > max_chars:
        raise RulesError(f"❌ Questions are limited to {max_chars} characters.")
    return q


def resolve_mime(filename: str, size: int, max_bytes: int) -> str:
    name = (filename or "").lower()
    dot = name.rfind(".")
    mime = ALLOWED_TYPES.get(name[dot:]) if dot != -1 else None
    if mime is None:
        raise RulesError("❌ Unsupported file type. Allowed: .pdf, .txt, .md, .docx")
    if size == 0:
        raise RulesError("❌ That file is empty.")
    if size > max_bytes:
        raise RulesError(
            f"❌ That file is {size // _MB} MB; the limit is {max_bytes // _MB} MB."
        )
    return mime


def _reject(url: str, host, addr, reason: str):
    logger.warning("Rejected non-public URL %s (host=%s, address=%s): %s",
                   url, host, addr, reason)
    raise RulesError(_PUBLIC_ONLY)


def check_public_url(url: str) -> None:
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as e:
        _reject(url, None, None, f"unparseable: {e!r}")
    if parsed.scheme not in ("http", "https") or not host:
        _reject(url, host, None, "bad scheme or missing host")
    try:
        infos = socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80))
    except (socket.gaierror, UnicodeError) as e:
        _reject(url, host, None, f"does not resolve: {e!r}")
    if not infos:
        _reject(url, host, None, "no addresses")
    for info in infos:
        addr = info[4][0].split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            _reject(url, host, addr, "invalid address")
        if not ip.is_global:
            _reject(url, host, addr, "non-global address")


class PublicOnlyResolver(aiohttp.ThreadedResolver):
    """Resolver that drops non-global addresses, closing the DNS-rebinding gap."""

    async def resolve(self, host, port=0, family=socket.AF_INET):
        results = await super().resolve(host, port, family)
        public = []
        for r in results:
            try:
                ok = ipaddress.ip_address(r["host"].split("%", 1)[0]).is_global
            except ValueError:
                ok = False
            if ok:
                public.append(r)
            else:
                logger.warning("Dropping non-public address %s for host %s",
                               r["host"], host)
        if not public:
            logger.warning("Host %s resolves only to non-public addresses", host)
            raise OSError(f"{host} resolves only to non-public addresses")
        return public


def create_fetch_session(timeout_s: float) -> "aiohttp.ClientSession":
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(resolver=PublicOnlyResolver()),
        timeout=aiohttp.ClientTimeout(total=timeout_s),
        trust_env=False,
    )


def _filename(headers, url: str) -> str:
    cd = headers.get("Content-Disposition")
    if cd:
        msg = Message()
        msg["content-disposition"] = cd
        fn = msg.get_filename()
        if fn:
            return fn
    return unquote(urlparse(url).path.rsplit("/", 1)[-1])


def _cap_filename(name: str, limit: int = 200) -> str:
    """Cap a remote-supplied filename, preserving the extension."""
    if len(name) <= limit:
        return name
    stem, dot, ext = name.rpartition(".")
    if dot and 0 < len(ext) <= 10:
        return stem[:limit - len(ext) - 1] + "." + ext
    return name[:limit]


async def fetch_url(session: "aiohttp.ClientSession", url: str, max_bytes: int,
                    timeout_s: float, max_redirects: int = 3):
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    current = url
    try:
        for _ in range(max_redirects + 1):
            try:
                await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        None, check_public_url, current),
                    timeout_s)
            except asyncio.TimeoutError:
                _reject(current, urlparse(current).hostname, None,
                        "address check timed out")
            async with session.get(current, allow_redirects=False,
                                   timeout=timeout) as resp:
                status = resp.status
                if status in _REDIRECTS:
                    loc = resp.headers.get("Location")
                    if not loc:
                        raise RulesError(
                            f"❌ Couldn't download that link (HTTP {status}).")
                    current = urljoin(current, loc)
                    continue
                if not 200 <= status < 300:
                    raise RulesError(
                        f"❌ Couldn't download that link (HTTP {status}).")
                buf = bytearray()
                async for chunk in resp.content.iter_chunked(65536):
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        resolve_mime("x.pdf", len(buf), max_bytes)
                data = bytes(buf)
                filename = _filename(resp.headers, current)
                try:
                    mime = resolve_mime(filename, len(data), max_bytes)
                except RulesError as e:
                    logger.info("Unrecognised extension for %s (%s); "
                                "checking Content-Type", filename, e.user_message)
                    ctype = resp.headers.get("Content-Type", "")
                    base = ctype.split(";", 1)[0].strip().lower()
                    if base != "application/pdf":
                        raise
                    filename = (filename or "download") + ".pdf"
                    mime = resolve_mime(filename, len(data), max_bytes)
                return data, _cap_filename(filename), mime
        raise RulesError("❌ Too many redirects.")
    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
        logger.warning("URL fetch failed for %s: %r", current, e)
        raise RulesError("❌ Couldn't download that link.")
