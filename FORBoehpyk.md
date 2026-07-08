# FOR BOEHPYK — What This Project Actually Is

## The elevator pitch

This repo is not "an app." There's no server, no database, no `npm install`, no build step, nothing to compile. It's a **frozen snapshot of a website** — specifically, Anna Soro's design portfolio (annasoro.com), pulled out of a hosted website-builder called **Adobe Portfolio** (you'll see it referred to internally as "myportfolio.com," its older brand name).

Think of it like the difference between a live restaurant kitchen and a photograph of a finished plate. Adobe Portfolio is the kitchen — it has the CMS, the drag-and-drop editor, the templating engine, the media processing pipeline. What landed in this folder is the plate: the final HTML, CSS, and JS that the kitchen produced, with all the templating already baked in and no oven left to put it back in.

## Why that matters for how you work here

Because there's no templating engine anymore, every single page — `index.html`, `about.html`, `logo-mat2030.html`, all ~40 of them — is a **complete, standalone document**. Each one carries its own full copy of the `<head>`, the navigation bar, the mobile nav overlay, the footer social icons, everything. Nothing is `<include>`d or componentized.

**The pitfall this creates:** if you ever need to change something global — say, swap out a social media link, or tweak the nav — you cannot do it once. You have to do it ~40 times, once per file, or you'll end up with a site where half the pages have the old link and half have the new one. This is the single most important thing to internalize about this codebase: **there is no source of truth for shared UI.** A good engineer's instinct in a normal codebase is "extract the duplication into a shared component." Here, that instinct is a trap — there's no templating layer to extract it *into*. The fix isn't cleverness, it's diligence: grep across every HTML file and edit them all consistently, or accept that the change should really happen back in the Adobe Portfolio CMS dashboard where the actual template lives, and treat this checkout as read-mostly.

## The pieces, and what's real vs. generated

- **The ~40 top-level `.html` files** are the actual content: portfolio project pages, gallery/index pages (`work.html`, `logodesign.html`, `print.html`, `video.html`, `motiondesign.html`), and static pages (`about.html`, `contact.html`).
- **`dist/css/main.css` and `dist/js/main.js`** are Adobe Portfolio's compiled theme — literally a minified webpack bundle with jQuery 2.2.4 baked in. This is machine output, not something a human wrote by hand, and it'll get silently regenerated and overwritten the next time the site is re-exported. Editing it directly is like scribbling corrections onto a printed photo instead of the negative — it won't survive the next print run.
- **`site/translations.js`** is tiny and easy to miss the significance of — it's just the i18n strings for form validation ("This field is required," etc.) used by the contact form.
- **No local images.** Every image, video, and font is fetched from `cdn.myportfolio.com` or Typekit. This project has zero local media assets to manage — which is unusual if you're used to typical web projects where `/assets` or `/images` is half the repo.

## A naming trap I want to flag explicitly

Several files start with `copy-of-` — e.g. `copy-of-logo-mat2030.html` sitting right next to `logo-mat2030.html`. My first instinct on seeing that pattern was "duplicate file, probably safe to delete one." **That instinct was wrong.** I checked, and these are genuinely different projects with different titles and content (`logo-mat2030.html` is "Logo Mat2030," `copy-of-logo-mat2030.html` is "Logo Design | Mat2030" — a different case study entirely). The `copy-of-` prefix is just Adobe Portfolio's way of auto-resolving a slug collision during export, not a signal about redundancy.

**The lesson generalizes:** in any codebase you didn't build yourself, filename patterns that look like obvious artifacts (duplicates, backups, `_old`, `copy`) deserve a quick verification pass before you act on the assumption. It costs one `diff` or `grep` and saves you from deleting someone's actual work.

## Why there's no build/lint/test setup, and why that's fine

This isn't a gap or an oversight — a statically-exported CMS site has nothing to build, lint, or test in the conventional sense. There's no JS logic of consequence to unit-test (the interactive bits are all inside the vendor bundle), and no compile step because the HTML is already the final artifact. The closest thing to "testing a change" here is opening the file in a browser, which is honestly the correct level of rigor for this kind of project — don't over-engineer tooling onto something that doesn't need it.

## The one transferable engineering lesson here

The broader habit worth keeping from a project like this: **before editing, understand what layer you're actually looking at.** Is this the source of truth, or a compiled/exported artifact of a source of truth that lives somewhere else (in this case, the Adobe Portfolio dashboard)? Editing generated output feels productive in the moment but the work evaporates on the next export. Good engineers develop a nose for "is this the real thing or a photograph of the real thing" before they start changing it — and that instinct applies just as much to a minified vendor bundle as it does to a compiled binary or a generated migration file.
