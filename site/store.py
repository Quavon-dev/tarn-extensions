import argparse
import html
import json
import re
import shutil
import struct
import sys
import unicodedata
import zlib
from pathlib import Path
from urllib.parse import urlsplit

SITE = "https://extension.tarnbrowser.com"
DOMAIN = "extension.tarnbrowser.com"
KIND = "tarn-extension-catalog"
STATIC = Path(__file__).resolve().parent / "static"
LANDING = "https://tarnbrowser.com/"
GET_TARN = LANDING + "#get-tarn"
PRIVACY = LANDING + "privacy#extension-store"
IMPRINT = LANDING + "imprint"
ARROW = '<span class="arrow" aria-hidden="true"><svg viewBox="0 0 16 16" width="14" height="14"><path d="M3 8h10M9 4l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></span>'

CATEGORIES = {
    "privacy-security": "Privacy & Security",
    "productivity": "Productivity",
    "reading": "Reading",
    "appearance": "Appearance",
    "accessibility": "Accessibility",
    "developer": "Developer Tools",
    "shopping": "Shopping",
    "social": "Social & Communication",
    "other": "Other",
}

PERMISSIONS = {
    "activeTab": "Clicking its toolbar button gives it the page you are on, in that tab",
    "alarms": "Run tasks on a schedule",
    "clipboardRead": "Read what you copy",
    "clipboardWrite": "Put text on your clipboard",
    "contextMenus": "Add items to the right-click menu",
    "cookies": "Read and change cookies on the sites you allow",
    "declarativeNetRequest": "Block or change network requests by rules",
    "declarativeNetRequestFeedback": "See which of its blocking rules matched",
    "declarativeNetRequestWithHostAccess": "Block or change network requests on the sites you allow",
    "menus": "Add items to the right-click menu",
    "nativeMessaging": "Talk to an app installed on your device",
    "notifications": "Show notifications",
    "scripting": "Run scripts on the sites you allow",
    "storage": "Keep its own data on this device",
    "tabs": "See the titles and addresses of your tabs",
    "unlimitedStorage": "Keep more data on this device than usual",
    "webNavigation": "See when pages load in your tabs",
    "webRequest": "See network requests on the sites you allow",
}

ID = re.compile(r"[a-z0-9-]{1,64}")
HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}")
MATCH = re.compile(r"(\*|https?|wss?|ftp)://(\*|(\*\.)?([^\s/*@:\\\[\]?#]+|\[[0-9A-Fa-f:.]+\]))(?::(\*|[0-9]{1,5}))?/\S*")
LOCAL_FILES = re.compile(r"file://(?:localhost)?/\S*")
OPAQUE = re.compile(r"(?:data|urn):\S+")
PERMISSION = re.compile(r"[A-Za-z][A-Za-z0-9._]{0,63}")
INVISIBLE = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}
FILLERS = {0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x2800, 0x3164, 0xFFA0, *range(0x180B, 0x1810), *range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0)}
MARKS = {"Mn", "Me"}
MAX_MARKS = 2
LIMITS = {"name": 64, "description": 300, "author": 80, "version": 64, "homepage": 300}
ITEM_LIMIT = 256
FIELDS = {
    "id": str, "name": str, "description": str, "version": str, "url": str, "sha256": str, "size": int,
    "author": str, "homepage": str, "category": str, "icon": str,
}
LISTS = ("permissions", "optional_permissions", "hosts", "screenshots")
ICON_BYTES = 256 << 10
ICON_PX = (128, 512)
SHOT_BYTES = 1 << 20
SHOT_PX = 2560
MAX_SHOTS = 5
PNG_DEPTHS = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
PNG_TEXT = {b"tEXt", b"zTXt", b"iTXt"}
PNG_TEXT_BYTES = 4096
JPEG_METADATA = {0xE1: "Exif or XMP", 0xED: "IPTC", 0xFE: "a comment"}
STRIP = "export the image again without metadata (Exif, XMP, IPTC, comments), which can hold the camera, place or author"
CSP = "default-src 'self'; img-src 'self'; script-src 'self'; style-src 'self'; base-uri 'none'; form-action 'self'"


class Invalid(Exception):
    pass


def png_size(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise Invalid("not a PNG")
    i, size, chunk, text, idat = 8, None, b"", 0, False
    while chunk != b"IEND":
        if i + 12 > len(data):
            raise Invalid("PNG ends before its IEND chunk")
        length, chunk = struct.unpack(">I4s", data[i : i + 8])
        end = i + 12 + length
        if end > len(data) or zlib.crc32(data[i + 4 : end - 4]) != struct.unpack(">I", data[end - 4 : end])[0]:
            raise Invalid("damaged PNG chunk")
        if (i == 8) != (chunk == b"IHDR"):
            raise Invalid("PNG must start with one IHDR chunk")
        if chunk == b"IHDR":
            if length != 13:
                raise Invalid("PNG IHDR must be 13 bytes")
            size = struct.unpack(">II", data[i + 8 : i + 16])
            depth, colour, compression, filtering, interlace = data[i + 16 : i + 21]
            if depth not in PNG_DEPTHS.get(colour, ()) or compression or filtering or interlace > 1:
                raise Invalid("PNG has an invalid bit depth, colour type, compression, filter or interlace method")
        body = data[i + 8 : end - 4]
        if chunk == b"eXIf" or (chunk == b"iTXt" and body.startswith(b"XML:com.adobe.xmp\x00")):
            raise Invalid(f"PNG has metadata ({chunk.decode()}); {STRIP}")
        text += length if chunk in PNG_TEXT else 0
        idat = idat or chunk == b"IDAT"
        i = end
    if i != len(data):
        raise Invalid("data after the PNG's IEND chunk")
    if not idat:
        raise Invalid("PNG has no IDAT image data")
    if text > PNG_TEXT_BYTES:
        raise Invalid(f"PNG text chunks must total at most {PNG_TEXT_BYTES // 1024} KiB")
    return size


def jpeg_size(data):
    if data[:2] != b"\xff\xd8":
        raise Invalid("not a JPEG")
    i, size, scan = 2, None, False
    while True:
        if i + 2 > len(data) or data[i] != 0xFF:
            raise Invalid("damaged JPEG")
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker == 0xD9:
            break
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            i += 2
            continue
        if i + 4 > len(data):
            raise Invalid("damaged JPEG")
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if length < 2 or i + 2 + length > len(data):
            raise Invalid("damaged JPEG segment")
        if marker in JPEG_METADATA:
            raise Invalid(f"JPEG has metadata ({JPEG_METADATA[marker]}); {STRIP}")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if size is not None or length < 7:
                raise Invalid("JPEG must have exactly one frame header")
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            size = (width, height)
        i += 2 + length
        if marker == 0xDA:
            if size is None:
                raise Invalid("JPEG scan before its frame header")
            scan = True
            while i + 1 < len(data) and not (data[i] == 0xFF and data[i + 1] not in (0x00, *range(0xD0, 0xD8))):
                i += 1
    if size is None:
        raise Invalid("JPEG without a frame header")
    if not scan:
        raise Invalid("JPEG has no scan")
    if i + 2 != len(data):
        raise Invalid("data after the JPEG's end marker")
    return size


def image(root, entry_id, rel, pattern, max_bytes):
    if not re.fullmatch(rf"assets/{entry_id}/{pattern}", rel):
        raise Invalid(f"{rel!r} must match assets/{entry_id}/{pattern}")
    path = root / rel
    if path.is_symlink() or not path.is_file() or root.resolve() not in path.resolve().parents:
        raise Invalid(f"{rel} is missing or not a regular file")
    if path.stat().st_size > max_bytes:
        raise Invalid(f"{rel} must be at most {max_bytes // 1024} KiB")
    data = path.read_bytes()
    try:
        width, height = (png_size if rel.endswith(".png") else jpeg_size)(data)
    except (struct.error, IndexError, ValueError) as err:
        raise Invalid(f"{rel} is damaged") from err
    if not width or not height:
        raise Invalid(f"{rel} has no size")
    return {"path": rel, "bytes": len(data), "width": width, "height": height}


def hidden(c):
    return ord(c) in FILLERS or unicodedata.category(c) in INVISIBLE


def clean(value, key, limit):
    if not value.strip() or len(value) > limit or any(hidden(c) for c in value):
        raise Invalid(f"{key} must be one line of at most {limit} characters without control or invisible characters")
    text = unicodedata.normalize("NFD", value)
    if all(c.isspace() or unicodedata.category(c) in MARKS for c in text):
        raise Invalid(f"{key} must contain visible characters")
    marks = 0
    for c in text:
        marks = marks + 1 if unicodedata.category(c) in MARKS else 0
        if marks > MAX_MARKS:
            raise Invalid(f"{key} may have at most {MAX_MARKS} combining marks on one character")
    return value


def homepage_host(url):
    parts = urlsplit(url) if url.isascii() and "\\" not in url and not any(c.isspace() for c in url) else None
    host = parts and parts.hostname
    if not host or parts.scheme != "https" or host != parts.netloc or not HOST.fullmatch(host):
        raise Invalid("homepage must be an https URL with a plain ASCII host (punycode for others), no user, password or port")
    return host


def types(entry):
    for key, kind in FIELDS.items():
        value = entry.get(key)
        if not isinstance(value, kind) or isinstance(value, bool):
            raise Invalid(f"{key} must be {'text' if kind is str else 'a whole number'}")
    for key in LISTS:
        value = entry.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise Invalid(f"{key} must be a list of strings")


def store_fields(entry, root):
    types(entry)
    entry_id = entry["id"]
    for key, limit in LIMITS.items():
        clean(entry[key], key, limit)
    for key in LISTS:
        for value in entry.get(key, []):
            clean(value, key, ITEM_LIMIT)
    for value in entry.get("hosts", []):
        plain_host(value)
    for key in ("permissions", "optional_permissions"):
        for value in entry.get(key, []):
            if not PERMISSION.fullmatch(value) and (key == "permissions" or not is_pattern(value)):
                raise Invalid(f"{key} must list permission names" + (" or host match patterns" if key != "permissions" else ""))
    host = homepage_host(entry["homepage"])
    if entry["category"] not in CATEGORIES:
        raise Invalid(f"category must be one of {', '.join(CATEGORIES)}")
    if entry["icon"] != f"assets/{entry_id}/icon.png":
        raise Invalid(f"icon must be assets/{entry_id}/icon.png")
    icon = image(root, entry_id, entry["icon"], r"icon\.png", ICON_BYTES)
    if icon["width"] != icon["height"] or not ICON_PX[0] <= icon["width"] <= ICON_PX[1]:
        raise Invalid(f"icon must be square, {ICON_PX[0]} to {ICON_PX[1]} px")
    shots = entry.get("screenshots", [])
    if len(shots) > MAX_SHOTS or len(set(shots)) != len(shots):
        raise Invalid(f"screenshots must list at most {MAX_SHOTS} distinct paths")
    checked = [image(root, entry_id, s, r"[a-z0-9][a-z0-9_-]*\.(png|jpg)", SHOT_BYTES) for s in shots]
    for shot in checked:
        if shot["width"] > SHOT_PX or shot["height"] > SHOT_PX:
            raise Invalid(f"{shot['path']} must be at most {SHOT_PX} px wide and high")
    return {"icon": icon, "shots": checked, "host": host}


def check(catalog, root):
    if not isinstance(catalog, dict) or catalog.get("kind") != KIND or not isinstance(catalog.get("extensions"), list):
        raise Invalid(f"kind must be {KIND} with an extensions list")
    revoked = catalog.get("revoked", [])
    if not isinstance(revoked, list) or not all(isinstance(r, dict) and isinstance(r.get("id"), str) and isinstance(r.get("sha256"), str) for r in revoked):
        raise Invalid("revoked must list objects with a string id and sha256")
    seen, errors, checked = set(), [], {}
    for n, entry in enumerate(catalog["extensions"]):
        entry_id = entry.get("id") if isinstance(entry, dict) else None
        valid_id = isinstance(entry_id, str) and ID.fullmatch(entry_id)
        label = entry_id if valid_id else f"entry #{n + 1}"
        try:
            if not isinstance(entry, dict):
                raise Invalid("an entry must be an object")
            if not valid_id:
                raise Invalid("id must be lowercase letters, digits and - (at most 64)")
            if entry_id in seen:
                raise Invalid("duplicate id")
            seen.add(entry_id)
            checked[entry_id] = store_fields(entry, root)
        except (Invalid, ValueError) as err:
            errors.append(f"{label}: {err}")
    if errors:
        raise Invalid("\n".join(errors))
    return checked


def e(value):
    return html.escape(str(value), quote=True)


def is_pattern(value):
    try:
        plain_host(value)
    except Invalid:
        return False
    return True


def plain_host(pattern):
    if pattern == "<all_urls>":
        return "every site"
    if LOCAL_FILES.fullmatch(pattern):
        return "local files"
    match = MATCH.fullmatch(pattern)
    if not match and OPAQUE.fullmatch(pattern):
        return pattern
    if not match or (match.group(5) not in (None, "*") and int(match.group(5)) > 65535):
        raise Invalid(f"{pattern!r} is not a match pattern: <all_urls>, <scheme>://<host><path> with host *, *.<domain> or <domain> and an optional port, or file:///<path>")
    scheme, host, subdomains, name, port = match.groups()
    if scheme not in ("*", "http", "https"):
        return pattern
    site = "every site" if host == "*" else f"{name} and its subdomains" if subdomains else name
    return site + ("" if port is None else " (any port)" if port == "*" else f" (port {port})")


def describe(permission):
    return PERMISSIONS.get(permission, f"Uses the {permission} API")


def page(root, title, body, description=""):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{e(CSP)}">
<meta name="referrer" content="no-referrer">
<meta name="color-scheme" content="light dark">
<meta name="description" content="{e(description or 'Extensions for Tarn, reviewed and signed by the Tarn maintainers.')}">
<title>{e(title)}</title>
<link rel="stylesheet" href="{root}style.css">
<script src="{root}search.js" defer></script>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="top"><div class="wrap bar">
<a class="brand" href="{root}"><img src="{root}mark.svg" alt="" width="30" height="30">Tarn <span>Extensions</span></a>
<a class="pill outline" href="{GET_TARN}">Get Tarn</a>
</div></header>
<main id="main" class="wrap">
{body}
</main>
<footer class="bottom"><div class="wrap">
<div class="columns">
<div class="about">
<p class="brand"><img src="{root}mark.svg" alt="" width="28" height="28">Tarn <span>Extensions</span></p>
<p>Every extension here is reviewed by the Tarn maintainers and listed in the signed catalog.</p>
</div>
<nav aria-label="Store"><h2>Store</h2><ul>
<li><a href="{root}">All extensions</a></li>
<li><a href="{root}index.json">Signed catalog</a></li>
<li><a href="{root}index.sig">Signature</a></li>
<li><a href="https://github.com/Quavon-dev/tarn-extensions">Source and submissions</a></li>
</ul></nav>
<nav aria-label="Tarn"><h2>Tarn</h2><ul>
<li><a href="{LANDING}">tarnbrowser.com</a></li>
<li><a href="{GET_TARN}">Download</a></li>
<li><a href="{PRIVACY}">Privacy</a></li>
<li><a href="{IMPRINT}">Imprint</a></li>
</ul></nav>
</div>
<p class="fine"><a href="https://quavon.de">Made by Quavon</a><span>No cookies. No analytics.</span></p>
</div></footer>
</body>
</html>
"""


def search_form(root, category=None):
    hidden = f'<input type="hidden" name="category" value="{e(category)}">' if category else ""
    return f"""<form class="search" role="search" action="{root}" method="get">
<label class="visually-hidden" for="q">Search extensions</label>
<input id="q" name="q" type="search" placeholder="Search extensions" autocomplete="off" spellcheck="false">{hidden}
<button class="pill solid" type="submit">Search</button>
</form>"""


def chips(root, current):
    items = [f'<li><a class="chip" href="{root}"{" aria-current=page" if current is None else ""}>All</a></li>']
    for cat, label in CATEGORIES.items():
        mark = " aria-current=page" if cat == current else ""
        items.append(f'<li><a class="chip" data-category="{cat}" href="{root}category/{cat}/"{mark}>{e(label)}</a></li>')
    return f'<nav aria-label="Categories"><ul class="chips">{"".join(items)}</ul></nav>'


def card(root, entry):
    i = entry["id"]
    return f"""<li class="card" data-id="{i}" data-category="{entry['category']}">
<a href="{root}extension/{i}/">
<img src="{root}assets/{i}/icon.png" alt="" width="56" height="56">
<span class="card-text"><strong>{e(entry['name'])}</strong>
<span class="desc">{e(entry['description'])}</span>
<span class="cat">{e(CATEGORIES[entry['category']])}</span></span>
</a>
</li>"""


def listing(root, entries, category=None):
    cards = "".join(card(root, x) for x in entries)
    empty = "" if entries else '<p class="empty">No extensions here yet.</p>'
    return f"""{search_form(root, category)}
{chips(root, category)}
<p class="status" id="status" role="status" aria-live="polite"></p>
<ul class="grid" id="results">{cards}</ul>{empty}"""


def detail(entry, checked):
    icon, shots = checked["icon"], checked["shots"]
    i, name = entry["id"], entry["name"]
    perms = [describe(p) for p in entry.get("permissions", [])]
    optional = [describe(p) for p in entry.get("optional_permissions", []) if not is_pattern(p)]
    patterns = entry.get("hosts", []) + [p for p in entry.get("optional_permissions", []) if is_pattern(p)]
    hosts = list(dict.fromkeys(plain_host(h) for h in patterns))
    rows = "".join(f"<li>{e(p)}</li>" for p in perms) or "<li>No special permissions</li>"
    rows += "".join(f"<li>{e(p)} <span class=\"tag\">if you allow it</span></li>" for p in optional)
    sites = "".join(f"<li>{e(h)}</li>" for h in hosts) or "<li>No sites</li>"
    gallery = "".join(
        f'<li><a href="../../{s["path"]}"><img src="../../{s["path"]}" width="{s["width"]}" height="{s["height"]}" alt="{e(name)}, screenshot {n} of {len(shots)}" loading="lazy"></a></li>'
        for n, s in enumerate(shots, 1)
    )
    screenshots = (
        f'<section aria-labelledby="shots"><h2 id="shots">Screenshots</h2>'
        f'<div class="shots" role="region" aria-labelledby="shots" tabindex="0"><ul>{gallery}</ul></div></section>'
    )
    size = f"{entry['size'] / 1024:.0f} KB" if isinstance(entry.get("size"), int) else ""
    body = f"""<article class="detail">
<div class="hero">
<img src="../../{icon['path']}" alt="" width="96" height="96">
<div>
<h1>{e(name)}</h1>
<p class="byline">by {e(entry['author'])} · <a href="../../category/{entry['category']}/">{e(CATEGORIES[entry['category']])}</a></p>
<a class="pill solid" href="{SITE}/install/{i}">Install in Tarn{ARROW}</a>
</div>
</div>
<p class="lead">{e(entry['description'])}</p>
{screenshots if shots else ''}
<div class="facts">
<section><h2>What it can do</h2><ul>{rows}</ul></section>
<section><h2>Sites it may ask for</h2><ul>{sites}</ul><p class="note">Tarn asks you per site.</p></section>
<section><h2>Details</h2><dl>
<dt>Version</dt><dd>{e(entry['version'])}</dd>
{f'<dt>Size</dt><dd>{size}</dd>' if size else ''}
<dt>Homepage</dt><dd><a href="{e(entry['homepage'])}" rel="noopener noreferrer">{e(checked['host'])}</a></dd>
</dl></section>
</div>
</article>"""
    return page("../../", f"{name} · Tarn Extensions", body, entry["description"])


def install(entry):
    i, name = entry["id"], entry["name"]
    body = f"""<article class="install-page">
<img src="../../assets/{i}/icon.png" alt="" width="96" height="96">
<h1>Open this link in Tarn to install {e(name)}</h1>
<p>Tarn asks before it installs anything and shows what the extension can do. This page offers no download.</p>
<p class="url"><code>{SITE}/install/{i}</code></p>
<p class="actions"><a class="pill solid" href="{GET_TARN}">Get Tarn{ARROW}</a><a class="pill outline" href="../../extension/{i}/">About {e(name)}</a></p>
</article>"""
    return page("../../", f"Install {name} · Tarn Extensions", body)


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build(index_path, sig_path, root, out):
    raw = index_path.read_bytes()
    index = json.loads(raw)
    checked = check(index, root)
    revoked = {(r.get("id"), r.get("sha256")) for r in index.get("revoked", [])}
    entries = sorted((x for x in index["extensions"] if (x["id"], x.get("sha256")) not in revoked), key=lambda x: (x["name"].casefold(), x["id"]))
    if out.exists() and any(out.iterdir()):
        raise Invalid(f"{out} must be empty")
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.json").write_bytes(raw)
    (out / "index.sig").write_bytes(sig_path.read_bytes())
    (out / "CNAME").write_text(DOMAIN + "\n")
    shutil.copytree(STATIC, out, dirs_exist_ok=True)
    search = [{k: x[k] for k in ("id", "name", "description", "author", "category")} for x in entries]
    write(out / "search.json", json.dumps(search, ensure_ascii=False, indent=1) + "\n")
    intro = "<h1>Extensions <span class=\"lake\">for Tarn.</span></h1><p class=\"lead\">Reviewed and signed by the Tarn maintainers. Install with one click in Tarn.</p>"
    write(out / "index.html", page("./", "Tarn Extensions", intro + listing("./", entries)))
    for cat, label in CATEGORIES.items():
        members = [x for x in entries if x["category"] == cat]
        write(out / "category" / cat / "index.html", page("../../", f"{label} · Tarn Extensions", f"<h1>{e(label)}</h1>" + listing("../../", members, cat)))
    for x in entries:
        for asset in [checked[x["id"]]["icon"], *checked[x["id"]]["shots"]]:
            (out / asset["path"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / asset["path"], out / asset["path"])
        write(out / "extension" / x["id"] / "index.html", detail(x, checked[x["id"]]))
        write(out / "install" / x["id"] / "index.html", install(x))
    write(out / "404.html", page("/", "Not found · Tarn Extensions", '<h1>Not found</h1><p>This page does not exist. <a href="/">All extensions</a></p>'))
    return len(entries)


def main():
    parser = argparse.ArgumentParser(description="Check the catalog's store fields and build the store website.")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check")
    c.add_argument("catalog", type=Path)
    c.add_argument("--root", type=Path, default=Path("."))
    b = sub.add_parser("build")
    b.add_argument("--index", type=Path, required=True)
    b.add_argument("--sig", type=Path, required=True)
    b.add_argument("--root", type=Path, default=Path("."))
    b.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "check":
            n = len(check(json.loads(args.catalog.read_text(encoding="utf-8")), args.root))
            print(f"{n} entries ok")
        else:
            print(f"built {build(args.index, args.sig, args.root, args.out)} extensions into {args.out}")
    except (Invalid, ValueError, KeyError, OSError) as err:
        print(f"::error::{err}".replace("\n", "%0A"), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
