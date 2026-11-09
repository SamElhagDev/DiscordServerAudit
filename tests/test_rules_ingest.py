import unittest
from unittest import mock

from utils import rules_ingest as ri
from utils.rules_errors import RulesError

HOSTS = {
    "rules.example": "93.184.216.34",
    "internal.example": "10.0.0.5",
    "mixed.example": ["93.184.216.34", "10.0.0.5"],
    "v6.example": "::1",
}


def fake_getaddrinfo(host, port, *a, **kw):
    import socket
    val = HOSTS.get(host, host)
    if host not in HOSTS:
        try:
            import ipaddress
            ipaddress.ip_address(host)
        except ValueError:
            raise socket.gaierror("nope")
    addrs = val if isinstance(val, list) else [val]
    return [(socket.AF_INET6 if ":" in x else socket.AF_INET, 1, 6, "", (x, 0))
            for x in addrs]


class FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks

    async def iter_chunked(self, n):
        for c in self._chunks:
            yield c


class FakeResp:
    def __init__(self, status=200, headers=None, chunks=()):
        self.status = status
        self.headers = headers or {}
        self.content = FakeContent(list(chunks))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeSession:
    def __init__(self, responses):
        self.responses = dict(responses)
        self.calls = []

    def get(self, url, allow_redirects=False, timeout=None):
        self.calls.append((url, allow_redirects, timeout))
        return self.responses[url]


class SyncTests(unittest.TestCase):
    def assertRules(self, fn, *args, contains=""):
        with self.assertRaises(RulesError) as cm:
            fn(*args)
        self.assertIn(contains, cm.exception.user_message)

    def test_resolve_mime_accepts_uppercase_pdf(self):
        self.assertEqual(ri.resolve_mime("RULES.PDF", 10, 100), "application/pdf")

    def test_resolve_mime_rejects_exe(self):
        self.assertRules(ri.resolve_mime, "a.exe", 10, 100,
                         contains=".pdf, .txt, .md, .docx")

    def test_resolve_mime_rejects_empty(self):
        self.assertRules(ri.resolve_mime, "a.pdf", 0, 100, contains="empty")

    def test_resolve_mime_rejects_oversize(self):
        self.assertRules(ri.resolve_mime, "a.pdf", 60 * 1048576, 50 * 1048576,
                         contains="60 MB; the limit is 50 MB")

    def test_validate_game_name_collapses_whitespace(self):
        self.assertEqual(ri.validate_game_name("  Twilight   Imperium "),
                         "Twilight Imperium")

    def test_validate_game_name_rejects_empty_and_long(self):
        self.assertRules(ri.validate_game_name, "   ", contains="1–100")
        self.assertRules(ri.validate_game_name, "x" * 101, contains="1–100")

    def test_validate_question(self):
        self.assertEqual(ri.validate_question("  hi ", 10), "hi")
        self.assertRules(ri.validate_question, " ", 10, contains="Ask a question")
        self.assertRules(ri.validate_question, "x" * 11, 10, contains="limited to 10")

    @mock.patch("utils.rules_ingest.socket.getaddrinfo", fake_getaddrinfo)
    def test_check_public_url(self):
        ri.check_public_url("https://rules.example/r.pdf")
        for u in ("file:///etc/passwd", "http://127.0.0.1/r.pdf",
                  "http://169.254.169.254/latest", "http://mixed.example/",
                  "http://v6.example/", "http://nonexistent.invalid/", "http:///x"):
            self.assertRules(ri.check_public_url, u,
                             contains="Only public http(s) links")

    def test_check_public_url_rejects_file_scheme(self):
        self.assertRules(ri.check_public_url, "file:///etc/passwd",
                         contains="Only public http(s) links")


@mock.patch("utils.rules_ingest.socket.getaddrinfo", fake_getaddrinfo)
class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_redirect_to_private_address_is_rejected(self):
        s = FakeSession({"http://rules.example/a": FakeResp(
            302, {"Location": "http://internal.example/r.pdf"})})
        with self.assertRaises(RulesError) as cm:
            await ri.fetch_url(s, "http://rules.example/a", 100, 5)
        self.assertIn("Only public http(s) links", cm.exception.user_message)
        self.assertEqual(len(s.calls), 1)
        self.assertFalse(s.calls[0][1])

    async def test_relative_redirect_followed(self):
        s = FakeSession({
            "http://rules.example/a": FakeResp(301, {"Location": "/b/r.pdf"}),
            "http://rules.example/b/r.pdf": FakeResp(200, {}, [b"abc"]),
        })
        data, name, mime = await ri.fetch_url(s, "http://rules.example/a", 100, 5)
        self.assertEqual((data, name, mime), (b"abc", "r.pdf", "application/pdf"))
        self.assertEqual(s.calls[0][2].total, 5)

    async def test_too_many_redirects(self):
        s = FakeSession({"http://rules.example/a": FakeResp(
            302, {"Location": "http://rules.example/a"})})
        with self.assertRaises(RulesError) as cm:
            await ri.fetch_url(s, "http://rules.example/a", 100, 5)
        self.assertEqual(cm.exception.user_message, "❌ Too many redirects.")

    async def test_redirect_without_location_and_bad_status(self):
        for resp in (FakeResp(302), FakeResp(404)):
            s = FakeSession({"http://rules.example/a": resp})
            with self.assertRaises(RulesError) as cm:
                await ri.fetch_url(s, "http://rules.example/a", 100, 5)
            self.assertIn("HTTP", cm.exception.user_message)

    async def test_fetch_aborts_over_max_bytes(self):
        consumed = []

        class CountingContent:
            async def iter_chunked(self, n):
                for c in (b"a" * 60, b"a" * 41, b"a" * 10, b"a" * 10):
                    consumed.append(c)
                    yield c
        resp = FakeResp(200, {})
        resp.content = CountingContent()
        s = FakeSession({"http://rules.example/r.pdf": resp})
        with self.assertRaises(RulesError):
            await ri.fetch_url(s, "http://rules.example/r.pdf", 100, 5)
        self.assertEqual(len(consumed), 2)

    async def test_fetch_uses_content_disposition_name(self):
        s = FakeSession({"http://rules.example/dl": FakeResp(
            200, {"Content-Disposition": 'attachment; filename="Root Law.pdf"'},
            [b"x"])})
        _, name, mime = await ri.fetch_url(s, "http://rules.example/dl", 100, 5)
        self.assertEqual((name, mime), ("Root Law.pdf", "application/pdf"))

    async def test_fetch_caps_long_filename(self):
        long_name = "a" * 500 + ".pdf"
        s = FakeSession({"http://rules.example/dl": FakeResp(
            200, {"Content-Disposition": f'attachment; filename="{long_name}"'},
            [b"x"])})
        _, name, mime = await ri.fetch_url(s, "http://rules.example/dl", 100, 5)
        self.assertEqual(len(name), 200)
        self.assertTrue(name.endswith(".pdf"))
        self.assertEqual(mime, "application/pdf")

    async def test_fetch_infers_pdf_from_content_type(self):
        s = FakeSession({"http://rules.example/download?id=3": FakeResp(
            200, {"Content-Type": "application/pdf; charset=binary"}, [b"x"])})
        _, name, mime = await ri.fetch_url(
            s, "http://rules.example/download?id=3", 100, 5)
        self.assertEqual(mime, "application/pdf")
        self.assertTrue(name.endswith(".pdf"))

    async def test_timeout_maps_to_rules_error(self):
        import asyncio

        class Boom:
            def get(self, *a, **k):
                raise asyncio.TimeoutError()
        with self.assertRaises(RulesError) as cm:
            await ri.fetch_url(Boom(), "http://rules.example/r.pdf", 100, 5)
        self.assertEqual(cm.exception.user_message, "❌ Couldn't download that link.")


class ResolverTests(unittest.IsolatedAsyncioTestCase):
    async def _resolve(self, results):
        async def fake(self_, host, port=0, family=0):
            return results
        with mock.patch("aiohttp.ThreadedResolver.resolve", fake):
            return await ri.PublicOnlyResolver().resolve("h.example", 80)

    async def test_resolver_drops_private_keeps_public(self):
        out = await self._resolve([{"host": "10.0.0.5"}, {"host": "93.184.216.34"}])
        self.assertEqual([r["host"] for r in out], ["93.184.216.34"])

    async def test_resolver_all_private_raises_oserror(self):
        with self.assertRaises(OSError):
            await self._resolve([{"host": "10.0.0.5"}, {"host": "127.0.0.1"}])

    async def test_create_fetch_session_uses_public_only_resolver(self):
        session = ri.create_fetch_session(5)
        try:
            self.assertIsInstance(session.connector._resolver,
                                  ri.PublicOnlyResolver)
        finally:
            await session.close()


if __name__ == "__main__":
    unittest.main()
