import copy
import json
import shutil
import struct
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
    "six screenshots": field("screenshots", ["assets/tab-tidy/icon.png"] * 6),
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


if __name__ == "__main__":
    unittest.main()
