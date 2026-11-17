import copy
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

import store

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png(width, height, pixels=True):
    rows = b"".join(b"\x00" + b"\x00" * width for _ in range(height)) if pixels else b""
    header = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
    return b"\x89PNG\r\n\x1a\n" + header + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def field(key, value):
    return lambda c, root: c["extensions"][2].__setitem__(key, value)


def screenshot(name, data):
    def apply(c, root):
        (root / "assets/tab-tidy" / name).write_bytes(data)
        c["extensions"][2]["screenshots"] = [f"assets/tab-tidy/{name}"]

    return apply


def icon(data):
    return lambda c, root: (root / "assets/tab-tidy/icon.png").write_bytes(data)


def symlink(c, root):
    (root / "assets/tab-tidy/evil.png").symlink_to("/etc/hosts")
    c["extensions"][2]["screenshots"] = ["assets/tab-tidy/evil.png"]


def fixture_bytes(path):
    return (FIXTURES / path).read_bytes()


def jpeg_frame(width, height):
    return b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"


def six_distinct(c, root):
    names = [f"assets/tab-tidy/shot{n}.png" for n in range(6)]
    for name in names:
        (root / name).write_bytes(png(16, 16))
    c["extensions"][2]["screenshots"] = names


EMPTY_IHDR = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", b"")


def png_with(*chunks, depth=8, colour=0, idat=True):
    header = chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, depth, colour, 0, 0, 0))
    data = chunk(b"IDAT", zlib.compress(b"\x00" * 17 * 16)) if idat else b""
    return b"\x89PNG\r\n\x1a\n" + header + b"".join(chunks) + data + chunk(b"IEND", b"")


def segment(marker, payload):
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def jpeg_with(*segments):
    data = fixture_bytes("assets/quiet-reader/dark.jpg")
    return data[:2] + b"".join(segments) + data[2:]


VALID_PATTERNS = {
    "<all_urls>": "every site",
    "*://*/*": "every site",
    "https://*/*": "every site",
    "*://*.example.org/*": "example.org and its subdomains",
    "*://*.example.org:8443/*": "example.org and its subdomains (port 8443)",
    "https://example.org/path/*": "example.org",
    "*://Example.com/*": "Example.com",
    "http://localhost/*": "localhost",
    "*://localhost:3000/*": "localhost (port 3000)",
    "http://localhost:*/*": "localhost (any port)",
    "http://127.0.0.1/*": "127.0.0.1",
    "http://[::1]:8080/*": "[::1] (port 8080)",
    "file:///*": "local files",
    "file:///Users/*": "local files",
    "wss://example.com/*": "wss://example.com/*",
    "ws://localhost:8080/socket": "ws://localhost:8080/socket",
    "ftp://example.com/*": "ftp://example.com/*",
    "urn:*": "urn:*",
}

KNOWN_PERMISSIONS = [
    "activeTab", "alarms", "clipboardRead", "clipboardWrite", "contextMenus", "cookies", "declarativeNetRequest",
    "declarativeNetRequestFeedback", "declarativeNetRequestWithHostAccess", "menus", "nativeMessaging",
    "notifications", "scripting", "storage", "tabs", "unlimitedStorage", "webNavigation", "webRequest",
]

VALID_TEXT = ["Cafe\u0301", "Vi\u1ec7t", "\u0e17\u0e35\u0e48", "Tab Tidy"]


INVALID = {
    "category outside the list": field("category", "games"),
    "category of the wrong type": field("category", ["reading"]),
    "http homepage": field("homepage", "http://example.com/"),
    "javascript homepage": field("homepage", "javascript:alert(1)"),
    "homepage with a user": field("homepage", "https://a@b.example/"),
    "homepage with a port": field("homepage", "https://example.com:8443/"),
    "homepage with a Cyrillic host": field("homepage", "https://\u0435xample.com/"),
    "homepage with a backslash": field("homepage", "https://evil.com\\.example.com/"),
    "homepage with U+2028": field("homepage", "https://example.com/\u2028x"),
    "homepage too long": field("homepage", "https://example.com/" + "a" * 281),
    "icon outside its folder": field("icon", "assets/tab-tidy/../quiet-reader/icon.png"),
    "icon not square": icon(fixture_bytes("assets/tracker-lens/panel.png")),
    "icon with data after IEND": icon(fixture_bytes("assets/tab-tidy/icon.png") + b"<script>"),
    "screenshot outside its folder": field("screenshots", ["assets/tab-tidy/../quiet-reader/light.png"]),
    "screenshot through a symlink": symlink,
    "GIF screenshot": field("screenshots", ["assets/tab-tidy/a.gif"]),
    "missing screenshot": field("screenshots", ["assets/tab-tidy/nope.png"]),
    "six screenshots": six_distinct,
    "the same screenshot twice": field("screenshots", ["assets/quiet-reader/light.png"] * 2),
    "PNG with an empty IHDR": screenshot("empty.png", EMPTY_IHDR + chunk(b"IEND", b"")),
    "PNG of only an empty IHDR": screenshot("bare.png", EMPTY_IHDR),
    "PNG with a 14-byte IHDR": screenshot("long.png", b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBBB", 16, 16, 8, 0, 0, 0, 0, 0)) + chunk(b"IEND", b"")),
    "JPEG with two frame headers": screenshot("two.jpg", b"\xff\xd8" + jpeg_frame(60000, 60000) + jpeg_frame(10, 10) + b"\xff\xd9"),
    "Hangul filler as the name": field("name", "\u3164"),
    "combining grapheme joiner in the name": field("name", "A\u034fB"),
    "halfwidth Hangul filler in the author": field("author", "Fixture\uffa0Labs"),
    "Hangul choseong filler in the name": field("name", "\u115fTab"),
    "Hangul jungseong filler in the name": field("name", "Tab\u1160"),
    "braille blank in the description": field("description", "Groups tabs\u2800."),
    "variation selector in the name": field("name", "Tab\ufe0fTidy"),
    "supplementary variation selector in the author": field("author", "Fixture\U000e0100"),
    "unassigned character in the name": field("name", "Tab\u0378"),
    "name of only wide spaces": field("name", "\u3000\u2003"),
    "author of only spaces": field("author", "   "),
    "host pattern with a user": field("hosts", ["https://a.com@b.com/*"]),
    "host pattern with a wildcard inside the host": field("hosts", ["*://ex*ample.com/*"]),
    "host pattern with a wildcard without a dot": field("hosts", ["*://*example.com/*"]),
    "host pattern without a path": field("hosts", ["*://example.com"]),
    "host pattern without a scheme": field("hosts", ["example.com"]),
    "optional host pattern with a user": field("optional_permissions", ["https://a.com@b.com/*"]),
    "host pattern with a user and password": field("hosts", ["https://user:pass@example.com/*"]),
    "host pattern with a backslash": field("hosts", ["https://evil.com\\a/*"]),
    "host pattern with a space": field("hosts", ["https://a b.com/*"]),
    "host pattern with the javascript scheme": field("hosts", ["javascript://x/*"]),
    "host pattern with the chrome scheme": field("hosts", ["chrome://settings/*"]),
    "host pattern with two wildcards": field("hosts", ["http://*.*.com/*"]),
    "host pattern with a broken IPv6 literal": field("hosts", ["https://[::1/*"]),
    "host pattern with a port above 65535": field("hosts", ["http://example.com:99999/*"]),
    "host pattern with an empty port": field("hosts", ["http://example.com:/*"]),
    "host pattern with an empty host": field("hosts", ["*://:80/*"]),
    "name of combining marks only": field("name", "\u0300\u0301"),
    "Khmer inherent vowel in the name": field("name", "Tab\u17b4Tidy"),
    "Khmer inherent vowel as the author": field("author", "\u17b5"),
    "three combining marks on one letter": field("name", "Tabe\u0301\u0302\u0303"),
    "PNG without IDAT": screenshot("noidat.png", png_with(idat=False)),
    "PNG with bit depth 4 in truecolour": screenshot("depth.png", png_with(depth=4, colour=2)),
    "PNG with an unknown colour type": screenshot("colour.png", png_with(colour=5)),
    "PNG with an eXIf chunk": screenshot("exif.png", png_with(chunk(b"eXIf", b"MM\x00*\x00\x00\x00\x08"))),
    "PNG with 5 KiB of text chunks": screenshot("text.png", png_with(chunk(b"tEXt", b"Comment\x00" + b"x" * 5000))),
    "JPEG without a scan": screenshot("noscan.jpg", b"\xff\xd8" + jpeg_frame(10, 10) + b"\xff\xd9"),
    "JPEG with Exif": screenshot("exif.jpg", jpeg_with(segment(0xE1, b"Exif\x00\x00MM\x00*"))),
    "JPEG with XMP": screenshot("xmp.jpg", jpeg_with(segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta/>"))),
    "JPEG with IPTC": screenshot("iptc.jpg", jpeg_with(segment(0xED, b"Photoshop 3.0\x00"))),
    "JPEG with a comment": screenshot("comment.jpg", jpeg_with(segment(0xFE, b"made on my phone"))),
    "screenshot 2561 px wide": screenshot("wide.png", png(2561, 1)),
    "screenshot 2561 px high": screenshot("high.png", png(1, 2561)),
    "PNG claiming a huge height": screenshot("tall.png", png(100, 1_000_000_000, pixels=False)),
    "PNG with a script after IEND": screenshot("script.png", png(100, 100) + b"<script>alert(1)</script>"),
    "PNG with data after IEND": screenshot("extra.png", fixture_bytes("assets/quiet-reader/light.png") + b"<script>"),
    "truncated PNG": screenshot("cut.png", fixture_bytes("assets/quiet-reader/light.png")[:-20]),
    "PNG with a bad CRC": screenshot("crc.png", fixture_bytes("assets/quiet-reader/light.png")[:-1] + b"\x00"),
    "JPEG with data after EOI": screenshot("extra.jpg", fixture_bytes("assets/quiet-reader/dark.jpg") + b"<script>"),
    "truncated JPEG": screenshot("cut.jpg", fixture_bytes("assets/quiet-reader/dark.jpg")[:-200]),
    "PNG named .jpg": screenshot("wrong.jpg", fixture_bytes("assets/quiet-reader/light.png")),
    "bidi control in the name": field("name", "Tab\u202eTidy"),
    "zero-width space and BOM in the name": field("name", "A\u200bB C\ufeff"),
    "soft hyphen in the author": field("author", "Fix\u00adture"),
    "paragraph separator in the description": field("description", "One\u2029Two"),
    "invisible character in a host": field("hosts", ["*://*.example.org/*\u200b"]),
    "name too long": field("name", "n" * 65),
    "description too long": field("description", "d" * 301),
    "author too long": field("author", "a" * 81),
    "empty author": field("author", ""),
    "hosts as a string": field("hosts", "*://*/*"),
    "id as a list": field("id", ["x"]),
    "id as a number": field("id", 7),
    "size as a string": field("size", "20480"),
    "size as a boolean": field("size", True),
    "entry not an object": lambda c, root: c["extensions"].append(["tab-tidy"]),
    "duplicate id": lambda c, root: c["extensions"].append(copy.deepcopy(c["extensions"][2])),
    "revoked entry not an object": lambda c, root: c.__setitem__("revoked", ["x"]),
}


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "root"
        shutil.copytree(FIXTURES, self.root)
        self.catalog = json.loads((FIXTURES / "catalog.json").read_text())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_fixtures_are_valid(self):
        self.assertEqual(sorted(store.check(self.catalog, self.root)), ["quiet-reader", "tab-tidy", "tracker-lens"])

    def test_invalid_entries_are_refused(self):
        for name, mutate in INVALID.items():
            with self.subTest(name):
                root = self.tmp / name.replace(" ", "-")
                shutil.copytree(FIXTURES, root)
                catalog = copy.deepcopy(self.catalog)
                mutate(catalog, root)
                with self.assertRaises(store.Invalid):
                    store.check(catalog, root)

    def test_refusals_name_their_reason(self):
        reasons = {
            "six screenshots": "at most 5 distinct",
            "the same screenshot twice": "at most 5 distinct",
            "PNG with an empty IHDR": "IHDR",
            "PNG of only an empty IHDR": "IHDR",
            "PNG with a 14-byte IHDR": "IHDR",
            "JPEG with two frame headers": "frame header",
            "PNG without IDAT": "IDAT",
            "PNG with bit depth 4 in truecolour": "bit depth",
            "PNG with an unknown colour type": "bit depth",
            "PNG with an eXIf chunk": "metadata",
            "PNG with 5 KiB of text chunks": "text",
            "JPEG without a scan": "scan",
            "JPEG with Exif": "metadata",
            "JPEG with XMP": "metadata",
            "JPEG with IPTC": "metadata",
            "JPEG with a comment": "metadata",
            "three combining marks on one letter": "combining marks",
            "name of combining marks only": "visible",
        }
        for name, reason in reasons.items():
            with self.subTest(name):
                root = self.tmp / ("reason-" + name.replace(" ", "-"))
                shutil.copytree(FIXTURES, root)
                catalog = copy.deepcopy(self.catalog)
                INVALID[name](catalog, root)
                with self.assertRaisesRegex(store.Invalid, reason):
                    store.check(catalog, root)

    def test_cli_reports_a_broken_image_as_an_error(self):
        self.catalog["extensions"][2]["screenshots"] = ["assets/tab-tidy/empty.png"]
        (self.root / "assets/tab-tidy/empty.png").write_bytes(EMPTY_IHDR)
        catalog = self.tmp / "catalog.json"
        catalog.write_text(json.dumps(self.catalog))
        script = Path(store.__file__)
        result = subprocess.run([sys.executable, "-I", str(script), "check", str(catalog), "--root", str(self.root)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("::error::tab-tidy: "), result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_every_match_pattern_tarn_accepts_is_accepted_and_read_as_a_site(self):
        self.catalog["extensions"][2]["hosts"] = list(VALID_PATTERNS)
        store.check(self.catalog, self.root)
        for pattern, shown in VALID_PATTERNS.items():
            with self.subTest(pattern):
                self.assertEqual(store.plain_host(pattern), shown)

    def test_plain_text_with_combining_marks_is_accepted(self):
        for text in VALID_TEXT:
            with self.subTest(text):
                self.catalog["extensions"][2]["name"] = text
                store.check(self.catalog, self.root)

    def test_small_text_chunks_are_accepted(self):
        (self.root / "assets/tab-tidy/note.png").write_bytes(png_with(chunk(b"tEXt", b"Software\x00" + b"x" * 100), colour=2))
        self.catalog["extensions"][2]["screenshots"] = ["assets/tab-tidy/note.png"]
        store.check(self.catalog, self.root)

    def test_permissions_read_as_plain_language(self):
        for name in KNOWN_PERMISSIONS:
            with self.subTest(name):
                self.assertIn(name, store.PERMISSIONS)
                self.assertNotEqual(store.describe(name), f"Uses the {name} API")
        self.assertEqual(store.describe("fooBar"), "Uses the fooBar API")

    def test_sites_are_listed_as_ones_it_may_ask_for(self):
        entry = self.catalog["extensions"][2]
        entry["permissions"] = ["storage", "fooBar"]
        entry["optional_permissions"] = ["tabs", "https://example.net/*"]
        page = store.detail(entry, store.check(self.catalog, self.root)["tab-tidy"])
        self.assertIn("<h2>Sites it may ask for</h2>", page)
        self.assertNotIn("Sites it works on", page)
        self.assertIn("<li>example.org and its subdomains</li>", page)
        self.assertIn("<li>example.net</li>", page)
        self.assertIn("<li>Uses the fooBar API</li>", page)
        self.assertNotIn("<li>fooBar</li>", page)
        self.assertNotIn("https://example.net/*", page)

    def test_catalog_must_be_an_object(self):
        with self.assertRaises(store.Invalid):
            store.check([], self.root)

    def test_build_is_deterministic_escaped_and_keeps_the_index(self):
        index = self.tmp / "index.json"
        index.write_text(json.dumps(self.catalog))
        sig = self.tmp / "index.sig"
        sig.write_bytes(b"\x01" * 64)
        outputs = []
        for n in range(2):
            out = self.tmp / f"site{n}"
            store.build(index, sig, self.root, out)
            outputs.append({p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()})
        self.assertEqual(outputs[0], outputs[1])
        site = outputs[0]
        self.assertEqual(site[Path("index.json")], index.read_bytes())
        self.assertEqual(site[Path("index.sig")], sig.read_bytes())
        page = site[Path("extension/tab-tidy/index.html")].decode()
        self.assertIn("Example &lt;Tools&gt; &amp; Co", page)
        self.assertNotIn("<Tools>", page)
        self.assertNotIn("frame-ancestors", page)
        self.assertIn(">example.org</a>", page)
        self.assertIn("<li>example.org and its subdomains</li>", page)
        self.assertIn("<li>every site</li>", site[Path("extension/tracker-lens/index.html")].decode())
        pages = [p for p in site if p.suffix == ".html"]
        self.assertGreater(len(pages), 10)
        for path in pages:
            with self.subTest(str(path)):
                html = site[path].decode()
                self.assertIn('<a href="https://tarnbrowser.com/imprint">Imprint</a>', html)
                self.assertIn('<a href="https://tarnbrowser.com/privacy#extension-store">Privacy</a>', html)


if __name__ == "__main__":
    unittest.main()
