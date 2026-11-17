# Tarn extensions

The official catalog of [Tarn](https://github.com/Quavon-dev/tarn-browser) extensions. Tarn shows
these under Settings › Extensions › Official and labels them **Official**.

Tarn extensions are Manifest V3 packages whose `manifest.json` sets
`browser_specific_settings.tarn.runtime` to `1`. They run the same in every Tarn client (macOS,
Windows, Linux, iOS, Android). The supported subset is described in Tarn's
[`docs/shared-core.md`](https://github.com/Quavon-dev/tarn-browser/blob/main/docs/shared-core.md).

## How the catalog works

- `catalog.json` lists the extensions. The **Sign catalog** workflow adds `version` and `created`,
  signs the result with the catalog's ed25519 key and publishes `index.json` and `index.sig` to the
  `catalog` release. Tarn downloads both from
  `https://github.com/Quavon-dev/tarn-extensions/releases/download/catalog/`.
- Tarn accepts an index only if its signature checks out against the public key built into the
  app, its `version` is not older than the last one it saw, and it is at most 30 days old. The
  workflow signs again every week so the index stays fresh.
- Every package is mirrored as a release asset of this repository; Tarn never fetches a catalog
  package from anywhere else, and checks its SHA-256 at install.
- `revoked` lists packages (`id` and `sha256`) Tarn must turn off. Entries are never removed from it.

Public key (base64): `FyiZ87sfpiQlNnK4dT3Qny0hLTTa29LoWVVLx4AFVrA=`

The private key is the `CATALOG_SIGNING_KEY` secret of the `catalog-signing` environment, which
only `main` may use; the maintainer keeps an offline copy.

## Adding or updating an extension

1. Review the package's source and manifest; only the subset above installs.
2. Create a release in this repository tagged `<id>-<version>` and attach the package as a `.zip`.
3. Add or update its entry in `catalog.json`:

   ```json
   {
     "id": "example",
     "name": "Example",
     "description": "What it does, in one sentence.",
     "version": "1.0.0",
     "url": "https://github.com/Quavon-dev/tarn-extensions/releases/download/example-1.0.0/example.zip",
     "sha256": "<sha-256 of the zip, lowercase hex>",
     "size": 12345,
     "permissions": ["storage"],
     "optional_permissions": [],
     "hosts": ["*://*.example.com/*"],
     "author": "Example Author",
     "homepage": "https://example.com/",
     "category": "productivity",
     "icon": "assets/example/icon.png",
     "screenshots": ["assets/example/main.png"]
   }
   ```

   `id` uses lowercase letters, digits and `-` (at most 64). `permissions` and `hosts` must match
   the manifest; Tarn shows them in its consent sheet.

   The last five fields are for the store website only; Tarn ignores them.

   | Field | Rule |
   |---|---|
   | `author` | Who publishes the extension |
   | `homepage` | An `https` URL with a lowercase ASCII host (punycode for other scripts) and no user, password or port |
   | `category` | One of the categories below |
   | `icon` | Exactly `assets/<id>/icon.png`: a square PNG, 128 to 512 px, at most 256 KiB |
   | `screenshots` | 0 to 5 paths `assets/<id>/<name>.png` or `.jpg` (`<name>` in lowercase letters, digits, `-` and `_`), each a complete file of at most 1 MiB and 2560 × 2560 px |

   `name` (at most 64 characters), `description` (300), `author` (80), `version` (64) and `homepage`
   (300) are one line without control or invisible characters; Tarn rejects an index above 256 KiB.
   `hosts` lists every match pattern the package asks for, required or optional, word for word as Tarn
   reads it: `<all_urls>`, `<scheme>://<host><path>` (scheme `*`, `http`, `https`, `ws`, `wss` or `ftp`;
   host `*`, `*.<domain>`, `<domain>` or an IP literal, with an optional port; no user or password)
   `file:///<path>`, `data:…` or `urn:…`. The store shows them as the sites the extension may ask for.

   | `category` | Shown as |
   |---|---|
   | `privacy-security` | Privacy & Security |
   | `productivity` | Productivity |
   | `reading` | Reading |
   | `appearance` | Appearance |
   | `accessibility` | Accessibility |
   | `developer` | Developer Tools |
   | `shopping` | Shopping |
   | `social` | Social & Communication |
   | `other` | Other |

4. Add the icon and screenshots under `assets/<id>/`.
5. Open a pull request. The **Check** workflow validates every entry. Once it is merged into `main`,
   the workflow signs and publishes the new index and rebuilds the store website.

## Store website

[extension.tarnbrowser.com](https://extension.tarnbrowser.com) lists the catalog with categories,
search and an **Install in Tarn** button per extension, which links to
`https://extension.tarnbrowser.com/install/<id>`. Tarn recognises that link when you click it and
opens its own consent sheet for the extension from its verified catalog copy; the website has no
other channel into the browser.

After every signing run, the **Sign catalog** workflow builds the site from the index it just
signed and deploys it to GitHub Pages. `site/store.py` (Python 3, standard library only) checks the
store fields and writes plain HTML, the stylesheet and a small search script; `/index.json` and
`/index.sig` are the signed files, byte for byte, and `/search.json` lists id, name, description,
author and category. The pages load nothing from other sites: the Geist and Funnel Display fonts
are served from `site/static/fonts/` under the SIL Open Font License, whose text sits next to them.

GitHub Pages cannot send response headers, so each page sets its Content Security Policy in a
`<meta>` tag. Browsers ignore `frame-ancestors` there, so another site can frame the store; that
gives it nothing, because Tarn acts only on an install link the user opens in a top-level tab and
never on one inside a frame.

Build it locally from a signed index:

```sh
python3 -I -m unittest discover -s site
python3 -I site/store.py check catalog.json
python3 -I site/store.py build --index index.json --sig index.sig --root . --out _site
python3 -m http.server --bind 127.0.0.1 --directory _site
```

`site/fixtures/` holds a catalog of fictional extensions that the **Check** workflow signs with a
throwaway key and builds as a preview (the `fixture-site` artifact). It is never published.

## Trademarks

"Tarn" and the Tarn logo (`site/static/mark.svg`) are trademarks of Quavon UG
(haftungsbeschränkt) that this repository's licence does not cover; see [`TRADEMARKS.md`](TRADEMARKS.md).
