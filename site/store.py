import argparse
import html
import json
import re
import shutil
import struct
import sys
from pathlib import Path
from urllib.parse import urlsplit

SITE = "https://extension.tarnbrowser.com"
DOMAIN = "extension.tarnbrowser.com"
KIND = "tarn-extension-catalog"
STATIC = Path(__file__).resolve().parent / "static"
LANDING = "https://tarnbrowser.com/"
GET_TARN = LANDING + "#get-tarn"
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
    "storage": "Keep its own data on this device",
    "activeTab": "Read and change the page you are on, after you click it",
    "declarativeNetRequest": "Block or change network requests by rules",
}

BIDI = set("\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
ID = re.compile(r"[a-z0-9-]{1,64}")
ICON_BYTES = 256 << 10
ICON_PX = (128, 512)
SHOT_BYTES = 1 << 20
SHOT_WIDTH = 2560
MAX_SHOTS = 5
CSP = "default-src 'self'; img-src 'self'; script-src 'self'; style-src 'self'; base-uri 'none'; form-action 'self'"


class Invalid(Exception):
    pass


def png_size(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR" or len(data) < 24:
        raise Invalid("not a PNG")
    return struct.unpack(">II", data[16:24])


def jpeg_size(data):
    if data[:3] != b"\xff\xd8\xff":
        raise Invalid("not a JPEG")
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            raise Invalid("damaged JPEG")
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        i += 2 + length
    raise Invalid("JPEG without a frame header")


def image(root, entry_id, rel, pattern):
    if not isinstance(rel, str) or not re.fullmatch(rf"assets/{entry_id}/{pattern}", rel):
        raise Invalid(f"{rel!r} must match assets/{entry_id}/{pattern}")
    path = root / rel
    if path.is_symlink() or not path.is_file() or root.resolve() not in path.resolve().parents:
        raise Invalid(f"{rel} is missing or not a regular file")
    data = path.read_bytes()
    size = (png_size if rel.endswith(".png") else jpeg_size)(data)
    return {"path": rel, "bytes": len(data), "width": size[0], "height": size[1]}


def text(entry, key):
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 or 0x7f <= ord(c) <= 0x9f or c in BIDI for c in value):
        raise Invalid(f"{key} must be a non-empty single-line string without control characters")
    return value


def store_fields(entry, root):
    entry_id = entry.get("id")
    if not isinstance(entry_id, str) or not ID.fullmatch(entry_id):
        raise Invalid("id must be lowercase letters, digits and - (at most 64)")
    for key in ("name", "description", "author", "version"):
        text(entry, key)
    homepage = text(entry, "homepage")
    parts = urlsplit(homepage)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or " " in homepage:
        raise Invalid("homepage must be an https URL")
    if entry.get("category") not in CATEGORIES:
        raise Invalid(f"category must be one of {', '.join(CATEGORIES)}")
    for key in ("permissions", "optional_permissions", "hosts"):
        value = entry.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise Invalid(f"{key} must be a list of strings")
    if entry.get("icon") != f"assets/{entry_id}/icon.png":
        raise Invalid(f"icon must be assets/{entry_id}/icon.png")
    icon = image(root, entry_id, entry["icon"], r"icon\.png")
    if icon["width"] != icon["height"] or not ICON_PX[0] <= icon["width"] <= ICON_PX[1]:
        raise Invalid(f"icon must be square, {ICON_PX[0]} to {ICON_PX[1]} px")
    if icon["bytes"] > ICON_BYTES:
        raise Invalid("icon must be at most 256 KiB")
    shots = entry.get("screenshots", [])
    if not isinstance(shots, list) or len(shots) > MAX_SHOTS or len(set(map(str, shots))) != len(shots):
        raise Invalid(f"screenshots must list at most {MAX_SHOTS} distinct paths")
    checked = [image(root, entry_id, s, r"[a-z0-9][a-z0-9_-]*\.(png|jpg)") for s in shots]
    for shot in checked:
        if shot["bytes"] > SHOT_BYTES or not 0 < shot["width"] <= SHOT_WIDTH or shot["height"] == 0:
            raise Invalid(f"{shot['path']} must be at most 1 MiB and {SHOT_WIDTH} px wide")
    return icon, checked


def check(catalog, root):
    if catalog.get("kind") != KIND or not isinstance(catalog.get("extensions"), list):
        raise Invalid(f"kind must be {KIND} with an extensions list")
    seen, errors, checked = set(), [], {}
    for n, entry in enumerate(catalog["extensions"]):
        label = entry.get("id", f"#{n}") if isinstance(entry, dict) else f"#{n}"
        try:
            if not isinstance(entry, dict):
                raise Invalid("an entry must be an object")
            if entry.get("id") in seen:
                raise Invalid("duplicate id")
            seen.add(entry.get("id"))
            checked[entry["id"]] = store_fields(entry, root)
        except (Invalid, ValueError) as err:
            errors.append(f"{label}: {err}")
    if errors:
        raise Invalid("\n".join(errors))
    return checked


def e(value):
    return html.escape(str(value), quote=True)


def plain_host(pattern):
    if pattern in ("<all_urls>", "*://*/*", "http://*/*", "https://*/*"):
        return "every site"
    host = urlsplit(pattern).netloc or pattern
    return f"{host[2:]} and its subdomains" if host.startswith("*.") else host


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
<li><a href="{LANDING}privacy">Privacy</a></li>
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


def detail(entry, icon, shots):
    i, name = entry["id"], entry["name"]
    perms = [PERMISSIONS.get(p, p) for p in entry.get("permissions", [])]
    optional = [PERMISSIONS.get(p, p) for p in entry.get("optional_permissions", [])]
    hosts = [plain_host(h) for h in entry.get("hosts", [])]
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
<section><h2>Sites it works on</h2><ul>{sites}</ul></section>
<section><h2>Details</h2><dl>
<dt>Version</dt><dd>{e(entry['version'])}</dd>
{f'<dt>Size</dt><dd>{size}</dd>' if size else ''}
<dt>Homepage</dt><dd><a href="{e(entry['homepage'])}" rel="noopener noreferrer">{e(urlsplit(entry['homepage']).hostname)}</a></dd>
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
        icon, shots = checked[x["id"]]
        for asset in [icon, *shots]:
            (out / asset["path"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / asset["path"], out / asset["path"])
        write(out / "extension" / x["id"] / "index.html", detail(x, icon, shots))
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
