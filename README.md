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

Public key (base64): `Bw6o96C9wLgBjfWcbvKknTWZqzEyWrCZLLtdI13ufIA=`

The private key exists only as the `CATALOG_SIGNING_KEY` secret of the `catalog-signing`
environment, which only `main` may use.

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
     "hosts": ["*://*.example.com/*"]
   }
   ```

   `id` uses lowercase letters, digits and `-` (at most 64). `permissions` and `hosts` must match
   the manifest; Tarn shows them in its consent sheet.
4. Open a pull request. Once it is merged into `main`, the workflow signs and publishes the new index.
