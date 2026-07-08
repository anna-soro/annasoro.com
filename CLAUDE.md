# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

This is a static export of Anna Soro's design portfolio site (annasoro.com), built and hosted through **Adobe Portfolio** (myportfolio.com). There is no build system, package manager, bundler, linter, or test suite in this repo — it's the raw HTML/CSS/JS that Adobe Portfolio generates when the site is exported/downloaded. All content editing (text, images, project ordering) normally happens in the Adobe Portfolio CMS dashboard; this checkout is for direct-file inspection/editing.

There are no build, lint, or test commands to run. Changes are validated by opening the HTML file directly in a browser (or serving the directory with any static file server, e.g. `python3 -m http.server`).

## Structure

The repo root is split into `app/` (site content — everything that gets served) and `infra/` (Docker/Traefik deployment files). See "Dockerized deployment" below for `infra/`.

- Every top-level `*.html` file under `app/` is a **fully self-contained, directly-servable static page** — no runtime templating or include system, same as the raw Adobe Portfolio export. However, the `<head>` boilerplate, responsive nav overlay, fixed site header, and footer/social-links block are logically de-duplicated by `scripts/sync_shared_html.py`: their source of truth lives in `scripts/fragments/` (shared markup) and `scripts/manifest/site_manifest.json` (per-page title/canonical/page-css/keywords/description/nav-active state), and the script renders them back into all 38 pages as plain HTML wrapped in `<!-- GENERATED:BEGIN/END -->` markers. See "Making site-wide changes" below and `docs/plan/dedupe-shared-html.md`.
- **Gallery/index pages** (`app/work.html`, `app/logodesign.html`, `app/print.html`, `app/video.html`, `app/motiondesign.html`) render a grid of `.project-cover` links, each pointing to one project's detail page.
- **Project detail pages** are the individual case-study files (e.g. `app/axfood-ab-templates-design.html`, `app/logo-mat2030.html`, `app/qr-library-project.html`, etc.), one per portfolio piece.
- `app/about.html` and `app/contact.html` are static content pages (contact.html includes a form wired to `app/site/translations.js` for client-side validation messages).
- `app/index.html` is the site entry point and currently canonicalizes to `work.html`.
- All internal asset references in the HTML use relative paths with no leading slash (e.g. `href="dist/css/main.css"`), so `app/` can be served with any docroot without a subpath assumption.

### `app/dist/` and `app/site/` are vendor/generated, not hand-authored

- `app/dist/css/main.css` and `app/dist/js/main.js` are Adobe Portfolio's own compiled theme bundle (webpack-built, minified, jQuery-based). Treat these as vendor output — they get regenerated on re-export, so avoid hand-editing unless you're intentionally patching the shipped bundle.
- `app/site/translations.js` holds i18n strings for form validation messages only.
- **Images, per-page CSS, and video are localized into `app/dist/images/`, `app/dist/css/pages/`, and `app/dist/video/`** (see `scripts/localize_assets.py` and `docs/plan/localize-remote-assets.md`) — the site no longer depends on `cdn.myportfolio.com` or `www-ccv.adobe.io` at request time for those asset types. Web fonts are the one exception left remote: Typekit (`use.typekit.net`) is intentionally not self-hosted (licensing), as is the single Vimeo embed and outbound behance.net/linkedin.com/t.me profile links. If Adobe Portfolio re-exports this site later (new remote URLs), rerun `python3 scripts/localize_assets.py --all` — it's idempotent via `scripts/asset_manifest.json`. The script resolves all HTML/`dist/` paths against `app/` (its `APP_ROOT` constant), not the repo root.

## Dockerized deployment

- `infra/Dockerfile`, `infra/docker-compose.yml`, and `infra/nginx.conf` build an `nginx:alpine` image serving `app/` as the docroot, and deploy it behind an external Traefik reverse proxy (network `traefik`, certresolver `le`) with `annasoro.com` canonical and `www.annasoro.com` 301-redirecting to it. See `docs/plan/` for the full deployment plan and runbook.
- The Docker build context is the repo root (`context: ..` from `infra/`), so `.dockerignore` must live at the repo root — Docker resolves it relative to the context root, not the Dockerfile's location.
- Deploys are manual: `git pull` on the VPS, then `cd infra && docker compose up -d --build`. No CI/CD or registry push is used.

### Naming quirk: `copy-of-*` files are not duplicates

Several files are prefixed `copy-of-` (e.g. `copy-of-logo-mat2030.html` vs `logo-mat2030.html`, `copy-of-cd-design-for-dutchpunch-music-festival.html`). These are **distinct project pages**, not stale copies — Adobe Portfolio's exporter appends this prefix when two pages would otherwise slug-collide. Check the `<title>` and masthead content before assuming redundancy or deleting one.

## Making site-wide changes

Nav items, social links, the fixed header, the footer, and the generic `<head>` boilerplate have a single source of truth under `scripts/fragments/` (shared markup) and `scripts/manifest/site_manifest.json` (per-page metadata: title, canonical, page CSS hash, keywords, description, which nav link is active). To make a site-wide change:

1. Edit the relevant fragment file under `scripts/fragments/`, or a page's entry in `scripts/manifest/site_manifest.json` for a per-page value (e.g. one page's keywords/description/title).
2. Run `python3 scripts/sync_shared_html.py --render && python3 scripts/sync_shared_html.py --verify`.
3. Review the `git diff` — changes should be confined inside the `<!-- GENERATED:BEGIN/END -->` marker spans in `app/*.html` — then commit the fragment/manifest changes together with the regenerated HTML files.

**Do not hand-edit the marked regions inside `app/*.html` directly** — those edits are silently overwritten on the next `--render` and won't propagate to the other 37 pages. Editing outside the marker spans (page-specific content, like a project's masthead text or gallery covers) is unaffected by this tooling and works as before.

This is a pre-deploy, host-side step (same operational pattern as `scripts/localize_assets.py`) — it produces final static HTML that gets committed to git; nothing about the Docker image or deploy flow changes. See `docs/plan/dedupe-shared-html.md` for the full design.
