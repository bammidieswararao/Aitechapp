# AiTechApp Telugu Fonts — GitHub mirror

This repository hosts the font catalog used by [aitechapp.in](https://aitechapp.in/telugu-fonts.html). The HTML is static, so InfinityFree does not serve font files.

## Published endpoints
- HTML: `telugu-fonts/telugu-fonts.html`
- Source inventory: `telugu-fonts/sources.json`
- Machine catalog: `telugu-fonts/public/fonts.json`
- Separate binaries: `telugu-fonts/public/fonts/*.ttf` / `*.otf`
- GitHub Action: `.github/workflows/telugu-fonts.yml`

## How it operates
A push to the source/import folders triggers the workflow. It verifies each Google Fonts source is governed by its own **SIL OFL** text, downloads the original binary, checks Telugu Unicode coverage using FontTools, extracts eligible user-supplied ZIPs, and commits individual binaries + `fonts.json`. The InfinityFree page automatically loads the JSON.

## GIST/C-DAC fonts
Free non-commercial *use* does not automatically permit a public downloadable mirror. GIST files are **not bundled**. Only archives with permission to **redistribute** may be added under `telugu-fonts/incoming/` and listed in `incoming/permissions.json` with a verifiable licence/permission evidence URL. The import workflow does not bypass source CAPTCHAs.

Do not publish any private or restricted font without appropriate authorization.

## Upload to InfinityFree
Download `telugu-fonts/telugu-fonts.html`, rename to `telugu-fonts.html` and place it in your `htdocs` folder. There is no PHP requirement.

## Build status
Check the GitHub Actions tab for the current mirror build. The initial `public/fonts.json` has no hosted entries until the Action finishes successfully.
