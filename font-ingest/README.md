# External Telugu Font Ingestion

This folder controls expansion beyond the current 121 direct-preview faces without changing the Telugu Fonts UI.

## Mobile-friendly workflow

1. Download or obtain the font archive/file from its legitimate source.
2. Upload it in GitHub to:
   - `fonts/incoming/unicode/` for SiliconAndhra / telugufonts.in / other Unicode collections.
   - `fonts/incoming/gist/` for GIST-TLOT files you manually obtained.
3. The **Ingest External Telugu Fonts** GitHub Action runs automatically.
4. It recursively reads ZIPs, accepts TTF/OTF, validates Unicode Telugu cmap coverage, reads family/style/weight/PostScript metadata, computes SHA-256, and deduplicates against the live catalogue.
5. For the same family/style/weight/width, TTF wins over OTF.
6. Approved new binaries are written to `fonts/unicode/` or `fonts/gist/`, and direct raw GitHub URLs are appended to `fonts-catalog.json`.
7. `scripts/sync_client_fallback.py` updates only the embedded fallback array in `telugu-fonts-client.js`.
8. The workflow hashes and validates `telugu-fonts.html` before and after processing so the restored five-button bottom navigation cannot be changed by ingestion.

## Redistribution gate

A font is auto-rehosted only when its embedded metadata clearly names a permissive licence such as SIL OFL, Apache-2.0, or MIT.

For any other binary, add an entry to `rehost-allowlist.json` only when you have documented permission to redistribute that exact font. A SHA-256 match is preferred.

GIST-TLOT pages currently describe the fonts as non-commercial free usage and require a human verification challenge. The metadata crawler intentionally **does not solve, OCR, submit, or bypass that challenge**. After a person completes the site's verification and downloads a permitted font, the file can be uploaded to `fonts/incoming/gist/` for metadata validation and dedupe.

## Deduplication

The ingest checks:

- SHA-256 binary hash
- Family + style + weight + width
- PostScript name for non-variable fonts
- Telugu Unicode cmap presence
- TTF preference over OTF for an otherwise identical face

See `gist-candidates.json`, `source-audit.json`, and `ingest-report.json` for audit details.
