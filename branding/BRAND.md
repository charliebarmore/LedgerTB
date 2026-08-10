# LedgerTB — Brand Guide

LedgerTB is a Ledger Labs product and wears the studio's identity: same palette,
same type system, its own mark. Do not invent new colors or fonts for LedgerTB
surfaces — extend from here.

## The name

**LedgerTB**, one word, capital L and capital TB. "TB" is the trial balance —
the report the whole product is built around. In prose the first mention can
carry the expansion: "LedgerTB (Ledger Trial Balance)". Never "Ledger TB",
"LedgerTb", or "ledgertb" in display copy. The domain is **ledgertb.com**.

In the wordmark, "Ledger" is Paper Cream and "TB" is Signal Orange.

## The mark

A **T-account that ties**. The Signal Orange T is the accountant's T-account;
the Paper Cream bars are entries in its debit and credit columns; the double
rule at the bottom is the accountant's mark under a total that balances. That
is the product's whole promise in one glyph: real double-entry, and it ties.

The mark sits upright — no slant. The studio's double-L glyph leans forward
(momentum, building); a balance symbol should stand still. Family membership
comes from the field, the palette, and the geometry.

Files in `logos/`:

| File | Use |
|---|---|
| `mark.svg` / `mark-*.png` | Primary. App icon source, avatars, anywhere on any background. |
| `mark-transparent.svg` | On Deep Teal / Abyss surfaces (no double field). |
| `mark-mono-dark.svg` | One color, Ink — light backgrounds, print. |
| `mark-mono-light.svg` | One color, Cream — quiet placements on dark. |
| `favicon.svg` / `favicon-*.png` / `favicon.ico` | Simplified (T + double rule only) for 16–64px. |
| `wordmark.svg`, `lockup-horizontal.svg` | Web only — text-based SVG needing Playfair Display. |

Rules: keep clear space of at least the crossbar's height around the mark;
don't recolor it, outline it, rotate it, or set it on white without switching
to `mark-mono-dark.svg`.

**Use the full mark everywhere it is drawn as vector or from a high-resolution
source** — it stays readable down to about 30px, which covers site headers and
footers, the app sidebar, and Dock icons. The simplified variant is only for
small *raster* renders where the column entries would turn to mush: browser
tab favicons, Windows taskbar `.ico` sizes, 16–32px PNGs. Two variants on one
surface reads as two logos, so pick by how the mark is being drawn, not by
how big it happens to be.

## Color

The Ledger Labs palette, unchanged. Tokens in `tokens/palette.css` / `.json`.

| Token | Hex | Role in LedgerTB |
|---|---|---|
| Deep Teal | `#1D434E` | Primary surface — hero, cards, the mark's field |
| Signal Orange | `#E8913A` | The T-account, links, accents, "TB" in the wordmark |
| Paper Cream | `#FDFCEA` | Body text on dark surfaces |
| Ledger Green | `#2D9148` | Calls-to-action, and *balanced/tied* states |
| Amber Gold | `#E6A532` | Gradient stop with orange; sparing highlights |
| Abyss | `#0F2A31` | Page base, deeper sections |
| Vellum | `#F5ECD6` | Light "paper" sections on the site |
| Ink | `#0B1A1F` | Text on light surfaces |

Ledger Green doing double duty is deliberate: in the app and on the site,
green means "this ties." Don't use it decoratively.

## Type

- **Playfair Display** (600/700) — headlines and editorial moments only.
- **Inter** (400/500/600) — everything else: UI, body, captions.
- **JetBrains Mono** (400/500) — figures, code, technical labels. Amounts in
  tables and anything a CPA would foot should be mono with tabular feel.

All from Google Fonts on the web. Documents and the app fall back to
Georgia / system sans / SF Mono.

## Voice

Written for accountants, in plain sentences. Say what happens and what to do.
Claims stay literal: "unbalanced entries never post" is a testable statement,
not marketing. The standing line on AI: **AI drafts, the professional
decides.** The user owns the books and the judgments in them.

## Relationship to the app's current UI

The Streamlit app still uses ProBooks-era navy (`#1f3a5f`) internally. The
rename pass will bring the in-app chrome, wordmark, and icons onto this guide;
until then this guide governs the site, the docs, and all new surfaces.
