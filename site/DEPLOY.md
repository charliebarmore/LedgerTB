# Deploying ledgertb.com

The static marketing site lives in `site/` and is hosted on **Cloudflare
Pages**, project **`ledgertb`**. The production custom domains are already
connected:

- https://ledgertb.com
- https://www.ledgertb.com
- https://ledgertb.pages.dev

## Redeploy after an edit

From the repository root:

```bash
wrangler pages deploy site --project-name=ledgertb --branch=main
```

This uploads the contents of `site/` directly to the production branch. The
Pages URL updates first; the two custom domains follow from the same deployment.

## Before announcing an update

1. Confirm every local image and icon referenced by `site/index.html` exists.
2. Confirm both evergreen release downloads return successfully:
   - `LedgerTB-mac.zip`
   - `LedgerTB-windows-x64-setup.exe`
3. Deploy the `site/` directory to the `main` branch.
4. Confirm the production domain and both download links return HTTP 200.

The release download URLs intentionally use GitHub's `releases/latest/download`
form, so publishing a future tagged release updates the buttons without another
site edit as long as the asset names stay the same.
