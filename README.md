# Aitechapp
Telugu fonts download

## Direct Telugu Font Pipeline

This repo now contains an automated GitHub pipeline for Telugu font ZIP packages.

- ZIP sources live in `sources.json`.
- The builder recursively inspects `.ttf` / `.otf` files.
- For the same family + style + weight, **TTF is always preferred over OTF**.
- OTF is used only when that face has no TTF.
- Duplicate binaries are removed by SHA-256.
- Duplicate logical faces are removed by family/style/weight.
- Selected files are written to `fonts/third-party/<source-id>/`.
- `fonts-catalog.json` contains only direct TTF/OTF raw URLs; ZIP and GitHub `/blob/` links are rejected.
- The web client can use each `font_raw_url` directly for CSS `@font-face` preview and Blob download.

The existing baseline is 84 direct Google/open Telugu font entries.

### Importing third-party ZIPs

The workflow does not silently assume redistribution permission. A source is imported when either:

1. its `redistribution_confirmed` value in `sources.json` is set to `true`, or
2. the manual workflow is run with the explicit re-hosting confirmation enabled.

After import, generated raw URLs follow this pattern:

`https://raw.githubusercontent.com/bammidieswararao/Aitechapp/main/fonts/third-party/<source>/<font-file>.ttf`

The browser-facing page should load `fonts-catalog.json` and never link users to GitHub web pages for downloads.
