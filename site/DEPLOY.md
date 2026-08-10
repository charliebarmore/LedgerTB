# Deploying ledgertb.com

The site is a single self-contained `index.html` plus two icons and the social
image. It is hosted on **Cloudflare Pages**, project **`ledgertb`**, in the
same account as `ledgerclaw.app` and `forepool.app`.

## Redeploy after an edit

From the repository root:

```bash
wrangler pages deploy site --project-name=ledgertb --branch=main
```

That is the whole deploy. Live within seconds at
https://ledgertb.pages.dev (and at ledgertb.com once DNS is pointed).

## Pointing ledgertb.com at it

The domain is registered at GoDaddy. Move its DNS to Cloudflare, the same way
`ledgerclaw.app` is set up — Cloudflare Pages cannot serve an apex domain
(`ledgertb.com` with no `www`) unless it controls the DNS, because apex records
cannot be CNAMEs and GoDaddy has no ALIAS/ANAME equivalent.

1. **Cloudflare dashboard → Add a site → `ledgertb.com` → Free plan.**
   It scans for existing records; a fresh domain has nothing worth keeping.
   Cloudflare then shows two nameservers, e.g. `xxx.ns.cloudflare.com`.
2. **GoDaddy → My Products → ledgertb.com → DNS → Nameservers → Change →
   "I'll use my own nameservers"** and paste Cloudflare's two. Save.
3. Wait for GoDaddy to hand over. Usually minutes; allow a few hours. Cloudflare
   emails when the zone goes active.
4. **Cloudflare dashboard → Workers & Pages → ledgertb → Custom domains →
   Set up a custom domain.** Add `ledgertb.com`, then repeat for
   `www.ledgertb.com`. Cloudflare creates the DNS records and issues the
   certificate itself.
5. Confirm: `curl -I https://ledgertb.com` returns `200`, and
   `https://www.ledgertb.com` reaches the same page.

### If you would rather leave DNS at GoDaddy

Workable but worse: add a CNAME for `www` → `ledgertb.pages.dev`, add
`www.ledgertb.com` as the Pages custom domain, and use GoDaddy's Domain
Forwarding to send the bare `ledgertb.com` to `https://www.ledgertb.com`.
The apex then depends on GoDaddy's redirect service, which is slower and has
been unreliable with HTTPS. Prefer the Cloudflare route.

## Before announcing the link

The Download buttons point at
`github.com/charliebarmore/LedgerTB/releases/latest/download/...`, which
returns 404 until the v1.0.0 release is **published** (it is currently a
draft) and the repository is public. Publish the release first, then share
the domain.
