# Design tokens — LedgerTB

Resolved tokens for this project live in **`branding/`**:

- `branding/BRAND.md` — the brand guide (name, mark, color roles, type, voice)
- `branding/tokens/palette.css` / `palette.json` — the palette
- `branding/logos/` — mark, wordmark, favicons, PNG renders
  (regenerate PNGs with `python scripts/make_brand_assets.py`)

LedgerTB inherits the **Ledger Labs studio palette** (Deep Teal `#1D434E`,
Signal Orange `#E8913A`, Paper Cream `#FDFCEA`, Ledger Green `#2D9148`) and
type system (Playfair Display / Inter / JetBrains Mono). Read `branding/BRAND.md`
before any UI or site work; don't pick colors fresh.

Known divergence: the in-app Streamlit chrome predates the rename and still
uses the ProBooks navy `#1f3a5f`. It converges on this guide in the rename
pass — new surfaces should use the brand tokens now.
