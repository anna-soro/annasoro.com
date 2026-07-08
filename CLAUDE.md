# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

This is a static export of Anna Soro's design portfolio site (annasoro.com), built and hosted through **Adobe Portfolio** (myportfolio.com). There is no build system, package manager, bundler, linter, or test suite in this repo — it's the raw HTML/CSS/JS that Adobe Portfolio generates when the site is exported/downloaded. All content editing (text, images, project ordering) normally happens in the Adobe Portfolio CMS dashboard; this checkout is for direct-file inspection/editing.

There are no build, lint, or test commands to run. Changes are validated by opening the HTML file directly in a browser (or serving the directory with any static file server, e.g. `python3 -m http.server`).

## Structure

- Every top-level `*.html` file is a **fully self-contained page** — each one repeats the entire `<head>` (meta tags, fonts, canonical URL), the responsive nav overlay, the fixed site header, and the social-links markup inline. There is no shared template, include, or partial system in this exported form.
- **Gallery/index pages** (`work.html`, `logodesign.html`, `print.html`, `video.html`, `motiondesign.html`) render a grid of `.project-cover` links, each pointing to one project's detail page.
- **Project detail pages** are the individual case-study files (e.g. `axfood-ab-templates-design.html`, `logo-mat2030.html`, `qr-library-project.html`, etc.), one per portfolio piece.
- `about.html` and `contact.html` are static content pages (contact.html includes a form wired to `site/translations.js` for client-side validation messages).
- `index.html` is the site entry point and currently canonicalizes to `work.html`.

### `dist/` and `site/` are vendor/generated, not hand-authored

- `dist/css/main.css` and `dist/js/main.js` are Adobe Portfolio's own compiled theme bundle (webpack-built, minified, jQuery-based). Treat these as vendor output — they get regenerated on re-export, so avoid hand-editing unless you're intentionally patching the shipped bundle.
- `site/translations.js` holds i18n strings for form validation messages only.
- All imagery, video, and web fonts are hosted externally on `cdn.myportfolio.com` / Typekit — there are no local `images/` or `assets/` directories to manage.

### Naming quirk: `copy-of-*` files are not duplicates

Several files are prefixed `copy-of-` (e.g. `copy-of-logo-mat2030.html` vs `logo-mat2030.html`, `copy-of-cd-design-for-dutchpunch-music-festival.html`). These are **distinct project pages**, not stale copies — Adobe Portfolio's exporter appends this prefix when two pages would otherwise slug-collide. Check the `<title>` and masthead content before assuming redundancy or deleting one.

## Making site-wide changes

Because nav, header, and social links are duplicated verbatim in every HTML file, any change that should apply site-wide (adding/removing a nav item, editing a social URL, changing the meta description pattern, etc.) must be applied consistently across **all** top-level HTML files — there's no single source of truth to edit once.
