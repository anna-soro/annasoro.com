#!/usr/bin/env python3
"""Sync shared HTML across the top-level app/*.html pages.

The <head> boilerplate, the responsive nav overlay, the fixed site header,
and the footer/social-links block are duplicated verbatim across all 38
top-level pages (see CLAUDE.md). This script treats those four regions as
single-sourced fragments (scripts/fragments/) plus a per-page metadata
manifest (scripts/manifest/site_manifest.json), and renders them back into
app/*.html as plain, final, directly-servable static HTML wrapped in
<!-- GENERATED:BEGIN/END --> markers. See docs/plan/dedupe-shared-html.md.

Usage:
    python3 scripts/sync_shared_html.py --extract [--reference-page FILE] [--force-extract]
    python3 scripts/sync_shared_html.py --render [--dry-run] [--pages a.html,b.html]
    python3 scripts/sync_shared_html.py --verify
    python3 scripts/sync_shared_html.py --all   # everyday round-trip: render + verify
                                                  # (--extract is a separate, one-time bootstrap
                                                  # step, guarded by --force-extract; --all never
                                                  # re-derives fragments/manifest from app/*.html)
"""
from __future__ import annotations

import argparse
import dataclasses
import difflib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = REPO_ROOT / "app"
HTML_FILES = sorted(APP_ROOT.glob("*.html"))
FRAGMENTS_DIR = REPO_ROOT / "scripts" / "fragments"
MANIFEST_PATH = REPO_ROOT / "scripts" / "manifest" / "site_manifest.json"
REPORTS_DIR = REPO_ROOT / "scripts" / "reports"

DEFAULT_REFERENCE_PAGE = "work.html"

NAV_TARGETS = {
    "about.html": "ABOUT",
    "logodesign.html": "LOGOS",
    "print.html": "PRINT",
    "video.html": "VIDEO",
    "motiondesign.html": "MOTION DESIGN",
    "contact.html": "CONTACT",
}

FRAGMENT_IDS = ["head_block", "nav_overlay", "site_header", "footer_block"]

FRAGMENT_FILENAMES = {
    "head_block": "head.html.tmpl",
    "nav_overlay": "nav_overlay.html.tmpl",
    "site_header": "site_header.html.tmpl",
    "footer_block": "footer.html.tmpl",
}

# Tier-1 bootstrap anchors — literal structural text, unique per file (verified
# via grep across all 38 pages before writing this script; see docs/plan).
REGION_RE = {
    "head_block": re.compile(r"<head>.*?</head>", re.DOTALL),
    "nav_overlay": re.compile(r'<div class="js-responsive-nav">.*?(?=<header class="site-header)', re.DOTALL),
    "site_header": re.compile(r'<header class="site-header.*?<div class="header-placeholder"></div>', re.DOTALL),
    "footer_block": re.compile(r'<footer class="site-footer".*?</footer>', re.DOTALL),
}

# Tier-2 steady-state anchors — inserted by the first successful render.
MARKER_RE = {
    fid: re.compile(rf'<!-- GENERATED:BEGIN {fid} -->.*?<!-- GENERATED:END {fid} -->', re.DOTALL)
    for fid in FRAGMENT_IDS
}

# Head slot patterns: 3 capture groups (prefix, value, suffix) so substitution
# never needs to reconstruct surrounding quotes/tags.
HEAD_SLOT_PATTERNS = {
    "keywords": re.compile(r'(<meta name="keywords"  content=")([^"]*)(" />)'),
    "description": re.compile(r'(<meta name="description"  content=")([^"]*)(" />)'),
    "og_title": re.compile(r'(<meta  property="og:title" content=")([^"]*)(" />)'),
    "og_description": re.compile(r'(<meta  property="og:description" content=")([^"]*)(" />)'),
    "page_css": re.compile(r'(href=")(dist/css/pages/[0-9a-f]+\.css)(")'),
    "canonical": re.compile(r'(<link rel="canonical" href=")([^"]*)(" />)'),
    "title": re.compile(r'(<title>)([^<]*)(</title>)'),
}
HEAD_SLOT_TOKENS = {
    "keywords": "{{KEYWORDS}}",
    "description": "{{DESCRIPTION}}",
    "og_title": "{{TITLE}}",  # og:title always equals title (verified); shares the TITLE token
    "og_description": "{{DESCRIPTION}}",  # og:description always equals description (verified)
    "page_css": "{{PAGE_CSS}}",
    "canonical": "{{CANONICAL}}",
    "title": "{{TITLE}}",
}
MANIFEST_HEAD_FIELDS = ["title", "canonical", "page_css", "keywords", "description"]

NAV_ACTIVE_RE = re.compile(r'class="active">')


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Region location
# ---------------------------------------------------------------------------

def _trim_trailing_ws(text: str, start: int, end: int) -> int:
    """nav_overlay's end anchor is a lookahead (it ends wherever <header
    begins), so its raw match span swallows the indentation whitespace that
    sits between the two elements. Trim it back so injected markers hug the
    actual content and leave surrounding whitespace undisturbed."""
    while end > start and text[end - 1] in " \t\n\r":
        end -= 1
    return end


def _extend_leading_ws(text: str, start: int) -> int:
    """Each region's opening tag is itself indented on its own line. The
    literal anchor patterns start exactly at the tag, so without this the
    line's leading indentation would end up stranded before the injected
    GENERATED:BEGIN comment instead of before the tag. Pull the span start
    back to the start of the line so the indentation travels with the
    fragment (identical depth across all 38 pages, verified before writing
    this script)."""
    while start > 0 and text[start - 1] in " \t":
        start -= 1
    return start


def find_region_span(text: str, fid: str) -> Optional[tuple]:
    """Prefer existing GENERATED markers (returning the *inner* span, markers
    excluded) over the Tier-1 literal anchors — this is what makes
    re-extraction safe on a file that's already been rendered once; without
    it the Tier-1 anchors would swallow the previous run's own marker
    comments into the "baseline" fragment, and the next render would nest a
    second marker pair inside the first."""
    marker_m = MARKER_RE[fid].search(text)
    if marker_m:
        begin_marker = f"<!-- GENERATED:BEGIN {fid} -->\n"
        end_marker = f"\n<!-- GENERATED:END {fid} -->"
        return marker_m.start() + len(begin_marker), marker_m.end() - len(end_marker)
    m = REGION_RE[fid].search(text)
    if not m:
        return None
    start, end = m.span()
    start = _extend_leading_ws(text, start)
    end = _trim_trailing_ws(text, start, end)
    return start, end


def find_region_text(text: str, fid: str) -> Optional[str]:
    span = find_region_span(text, fid)
    return None if span is None else text[span[0]:span[1]]


# ---------------------------------------------------------------------------
# Head slot extraction / normalization
# ---------------------------------------------------------------------------

def extract_head_slots(head_text: str) -> Optional[dict]:
    values = {}
    for slot, pattern in HEAD_SLOT_PATTERNS.items():
        m = pattern.search(head_text)
        if not m:
            return None
        values[slot] = m.group(2)
    if values["og_title"] != values["title"]:
        return None
    if values["og_description"] != values["description"]:
        return None
    return values


def normalize_head(head_text: str) -> str:
    text = head_text
    for slot, pattern in HEAD_SLOT_PATTERNS.items():
        token = HEAD_SLOT_TOKENS[slot]
        text = pattern.sub(lambda m, t=token: m.group(1) + t + m.group(3), text)
    return text


def render_head(template_text: str, page_meta: dict) -> str:
    text = template_text
    text = text.replace("{{KEYWORDS}}", page_meta["keywords"])
    text = text.replace("{{DESCRIPTION}}", page_meta["description"])
    text = text.replace("{{TITLE}}", page_meta["title"])
    text = text.replace("{{PAGE_CSS}}", page_meta["page_css"])
    text = text.replace("{{CANONICAL}}", page_meta["canonical"])
    return text


# ---------------------------------------------------------------------------
# Nav/header active-state
# ---------------------------------------------------------------------------

def normalize_nav(text: str) -> str:
    return NAV_ACTIVE_RE.sub(">", text)


def detect_nav_active(nav_text: str) -> Optional[str]:
    found = None
    for href, label in NAV_TARGETS.items():
        if f'href="{href}" class="active">{label}' in nav_text:
            if found is not None:
                return "__AMBIGUOUS__"
            found = href
    return found


def render_nav_like(baseline_text: str, nav_active: Optional[str]) -> str:
    if nav_active is None:
        return baseline_text
    label = NAV_TARGETS[nav_active]
    old = f'href="{nav_active}" >{label}</a>'
    new = f'href="{nav_active}" class="active">{label}</a>'
    count = baseline_text.count(old)
    if count != 1:
        raise ValueError(f"expected exactly 1 occurrence of nav target {nav_active!r}, found {count}")
    return baseline_text.replace(old, new, 1)


def render_fragment(fid: str, template_text: str, page_meta: dict) -> str:
    if fid == "head_block":
        return render_head(template_text, page_meta)
    if fid in ("nav_overlay", "site_header"):
        return render_nav_like(template_text, page_meta.get("nav_active"))
    if fid == "footer_block":
        return template_text
    raise ValueError(fid)


def marker_wrap(fid: str, content: str) -> str:
    return f"<!-- GENERATED:BEGIN {fid} -->\n{content}\n<!-- GENERATED:END {fid} -->"


# ---------------------------------------------------------------------------
# Fragments / manifest I/O
# ---------------------------------------------------------------------------

def load_fragments() -> dict:
    return {
        fid: (FRAGMENTS_DIR / fname).read_text(encoding="utf-8")
        for fid, fname in FRAGMENT_FILENAMES.items()
    }


def save_fragments(fragments: dict) -> None:
    FRAGMENTS_DIR.mkdir(parents=True, exist_ok=True)
    for fid, fname in FRAGMENT_FILENAMES.items():
        (FRAGMENTS_DIR / fname).write_text(fragments[fid], encoding="utf-8")


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def save_manifest(reference_page: str, pages: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reference_page": reference_page,
        "nav_targets": NAV_TARGETS,
        "pages": dict(sorted(pages.items())),
    }
    MANIFEST_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase A: extract
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ExtractResult:
    manifest_pages: dict
    fragments: dict
    mismatches: list


def run_extract(html_paths: list, reference_page: str) -> ExtractResult:
    ref_path = APP_ROOT / reference_page
    ref_text = ref_path.read_text(encoding="utf-8")

    ref_regions = {}
    for fid in FRAGMENT_IDS:
        text_region = find_region_text(ref_text, fid)
        if text_region is None:
            raise SystemExit(f"Could not locate {fid} in reference page {reference_page}")
        ref_regions[fid] = text_region

    ref_head_values = extract_head_slots(ref_regions["head_block"])
    if ref_head_values is None:
        raise SystemExit(f"Could not extract head slots from reference page {reference_page}")

    fragments = {
        "head_block": normalize_head(ref_regions["head_block"]),
        "nav_overlay": normalize_nav(ref_regions["nav_overlay"]),
        "site_header": normalize_nav(ref_regions["site_header"]),
        "footer_block": ref_regions["footer_block"],
    }

    mismatches: list = []
    manifest_pages: dict = {}

    for path in html_paths:
        text = path.read_text(encoding="utf-8")
        page = path.name
        regions = {}
        for fid in FRAGMENT_IDS:
            text_region = find_region_text(text, fid)
            if text_region is None:
                mismatches.append({"file": page, "fragment_id": fid, "reason": "region not found"})
                continue
            regions[fid] = text_region

        if "head_block" in regions:
            values = extract_head_slots(regions["head_block"])
            if values is None:
                mismatches.append({"file": page, "fragment_id": "head_block",
                                    "reason": "slot extraction failed or og:title/og:description mismatch"})
            else:
                normalized = normalize_head(regions["head_block"])
                if normalized != fragments["head_block"]:
                    diff = "\n".join(difflib.unified_diff(
                        fragments["head_block"].splitlines(), normalized.splitlines(), lineterm=""
                    ))
                    mismatches.append({"file": page, "fragment_id": "head_block",
                                        "reason": "normalized text differs from reference",
                                        "diff_preview": diff[:2000]})
                manifest_pages[page] = {
                    "title": values["title"],
                    "canonical": values["canonical"],
                    "page_css": values["page_css"],
                    "keywords": values["keywords"],
                    "description": values["description"],
                    "nav_active": None,
                }

        nav_active_by_fragment = {}
        for fid in ("nav_overlay", "site_header"):
            if fid not in regions:
                continue
            normalized = normalize_nav(regions[fid])
            if normalized != fragments[fid]:
                mismatches.append({"file": page, "fragment_id": fid,
                                    "reason": "normalized text differs from reference"})
            active = detect_nav_active(regions[fid])
            if active == "__AMBIGUOUS__":
                mismatches.append({"file": page, "fragment_id": fid,
                                    "reason": "more than one active nav link found"})
                active = None
            nav_active_by_fragment[fid] = active

        if "nav_overlay" in nav_active_by_fragment and "site_header" in nav_active_by_fragment:
            if nav_active_by_fragment["nav_overlay"] != nav_active_by_fragment["site_header"]:
                mismatches.append({"file": page, "fragment_id": "nav_overlay/site_header",
                                    "reason": (f"nav_active mismatch: overlay="
                                               f"{nav_active_by_fragment['nav_overlay']!r} header="
                                               f"{nav_active_by_fragment['site_header']!r}")})
        if page in manifest_pages:
            manifest_pages[page]["nav_active"] = nav_active_by_fragment.get("nav_overlay")

        if "footer_block" in regions and regions["footer_block"] != fragments["footer_block"]:
            mismatches.append({"file": page, "fragment_id": "footer_block", "reason": "differs from reference"})

    return ExtractResult(manifest_pages, fragments, mismatches)


# ---------------------------------------------------------------------------
# Phase B: render
# ---------------------------------------------------------------------------

def locate_spans(text: str, page: str, blocked: list) -> Optional[list]:
    spans = []
    ok = True
    for fid in FRAGMENT_IDS:
        marker_matches = list(MARKER_RE[fid].finditer(text))
        if len(marker_matches) == 1:
            spans.append((fid, marker_matches[0].span()))
            continue
        if len(marker_matches) > 1:
            blocked.append({"file": page, "fragment_id": fid, "reason": f"{len(marker_matches)} marker matches"})
            ok = False
            continue
        region_matches = list(REGION_RE[fid].finditer(text))
        if len(region_matches) != 1:
            blocked.append({"file": page, "fragment_id": fid, "reason": f"{len(region_matches)} region matches"})
            ok = False
            continue
        s, e = region_matches[0].span()
        s = _extend_leading_ws(text, s)
        e = _trim_trailing_ws(text, s, e)
        spans.append((fid, (s, e)))
    return spans if ok else None


def render_page_text(text: str, page: str, fragments: dict, manifest_pages: dict, blocked: list) -> Optional[str]:
    page_meta = manifest_pages.get(page)
    if page_meta is None:
        blocked.append({"file": page, "reason": "no manifest entry"})
        return None
    spans = locate_spans(text, page, blocked)
    if spans is None:
        return None
    spans.sort(key=lambda t: t[1][0], reverse=True)
    new_text = text
    for fid, (s, e) in spans:
        rendered = render_fragment(fid, fragments[fid], page_meta)
        new_text = new_text[:s] + marker_wrap(fid, rendered) + new_text[e:]
    return new_text


def run_render(html_paths: list, fragments: dict, manifest_pages: dict, dry_run: bool) -> dict:
    blocked: list = []
    planned: dict = {}
    for path in html_paths:
        text = path.read_text(encoding="utf-8")
        new_text = render_page_text(text, path.name, fragments, manifest_pages, blocked)
        if new_text is not None:
            planned[path] = new_text

    if blocked:
        log("RENDER BLOCKED — aborting, nothing written:")
        for b in blocked:
            log(f"  {b}")
        return {"aborted": True, "blocked": blocked, "changed": [], "unchanged": []}

    changed, unchanged = [], []
    for path, new_text in planned.items():
        old_text = path.read_text(encoding="utf-8")
        if new_text == old_text:
            unchanged.append(path.name)
            continue
        changed.append(path.name)
        if dry_run:
            diff = "\n".join(difflib.unified_diff(
                old_text.splitlines(), new_text.splitlines(),
                fromfile=path.name, tofile=path.name, lineterm=""
            ))
            log(diff)
        else:
            path.write_text(new_text, encoding="utf-8")

    return {"aborted": False, "blocked": [], "changed": changed, "unchanged": unchanged, "dry_run": dry_run}


# ---------------------------------------------------------------------------
# Phase C: verify
# ---------------------------------------------------------------------------

def verify_site(html_paths: list, fragments: dict, manifest_pages: dict) -> dict:
    checks: dict = {"idempotent": True, "manifest_drift": [], "structural": [], "diffs": {}}

    for path in html_paths:
        text = path.read_text(encoding="utf-8")
        page = path.name
        blocked: list = []
        rerendered = render_page_text(text, page, fragments, manifest_pages, blocked)
        if blocked:
            checks["idempotent"] = False
            checks["diffs"][page] = {"blocked": blocked}
            continue
        if rerendered != text:
            checks["idempotent"] = False
            diff = "\n".join(difflib.unified_diff(
                text.splitlines(), rerendered.splitlines(), fromfile=page, tofile=page, lineterm=""
            ))
            checks["diffs"][page] = diff[:2000]

        # Manifest drift: re-extract this page's fields from disk, compare to manifest.
        head_text = find_region_text(text, "head_block")
        if head_text is not None and page in manifest_pages:
            values = extract_head_slots(head_text)
            if values is None:
                checks["manifest_drift"].append({"file": page, "reason": "could not re-extract head slots"})
            else:
                current = manifest_pages[page]
                for field in MANIFEST_HEAD_FIELDS:
                    if values[field] != current[field]:
                        checks["manifest_drift"].append({
                            "file": page, "field": field,
                            "manifest": current[field], "on_disk": values[field],
                        })
            nav_text = find_region_text(text, "nav_overlay")
            active = detect_nav_active(nav_text) if nav_text is not None else None
            if active != current.get("nav_active"):
                checks["manifest_drift"].append({
                    "file": page, "field": "nav_active",
                    "manifest": current.get("nav_active"), "on_disk": active,
                })

        # Structural invariants
        header_open = list(re.finditer(r'<header class="site-header', text))
        if len(header_open) != 1:
            checks["structural"].append({"file": page, "issue": f"expected 1 site-header open, found {len(header_open)}"})
        else:
            after = text[header_open[0].end():]
            close_idx = after.find("</header>")
            nested = after[:close_idx].count("<header")
            if nested != 0:
                checks["structural"].append({"file": page, "issue": f"{nested} nested <header> before site-header's own close"})
        footer_open = list(re.finditer(r'<footer class="site-footer"', text))
        if len(footer_open) != 1:
            checks["structural"].append({"file": page, "issue": f"expected 1 site-footer, found {len(footer_open)}"})
        for fid in FRAGMENT_IDS:
            begins = len(re.findall(rf'<!-- GENERATED:BEGIN {fid} -->', text))
            ends = len(re.findall(rf'<!-- GENERATED:END {fid} -->', text))
            if begins != ends:
                checks["structural"].append({"file": page, "issue": f"{fid} marker imbalance: {begins} begin / {ends} end"})

    return checks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def write_report(phase: str, extra: dict) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = REPORTS_DIR / f"run_{ts}_{phase}.json"
    payload = {"phase": phase, "finished_at": datetime.now(timezone.utc).isoformat(), **extra}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log(f"Report written to {path}")
    return path


def resolve_pages(pages_arg: Optional[str]) -> list:
    if not pages_arg:
        return HTML_FILES
    names = {n.strip() for n in pages_arg.split(",") if n.strip()}
    return [p for p in HTML_FILES if p.name in names]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--extract", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pages", type=str, default=None)
    parser.add_argument("--reference-page", type=str, default=DEFAULT_REFERENCE_PAGE)
    parser.add_argument("--force-extract", action="store_true")
    args = parser.parse_args()

    if not any([args.extract, args.render, args.verify, args.all]):
        parser.print_help()
        return 2

    if args.extract:
        if MANIFEST_PATH.exists() and not args.force_extract:
            log(f"{MANIFEST_PATH} already exists — pass --force-extract to redo extraction "
                f"(this discards any hand-edits made to scripts/fragments/ since the last extract).")
            return 2
        log(f"Extracting fragments/manifest from {len(HTML_FILES)} pages (reference={args.reference_page})...")
        result = run_extract(HTML_FILES, args.reference_page)
        write_report("extract", {"mismatch_count": len(result.mismatches), "mismatches": result.mismatches})
        if result.mismatches:
            log(f"EXTRACT FAILED — {len(result.mismatches)} mismatch(es) found. Nothing written.")
            for m in result.mismatches[:20]:
                log(f"  {m}")
            return 2
        save_fragments(result.fragments)
        save_manifest(args.reference_page, result.manifest_pages)
        log(f"Extraction clean: wrote {len(FRAGMENT_FILENAMES)} fragment files and "
            f"{len(result.manifest_pages)} manifest entries.")

    if args.render or args.all:
        fragments = load_fragments()
        manifest = load_manifest()
        pages = resolve_pages(args.pages)
        log(f"Rendering {len(pages)} page(s){' (dry-run)' if args.dry_run else ''}...")
        result = run_render(pages, fragments, manifest["pages"], dry_run=args.dry_run)
        write_report("render", result)
        if result["aborted"]:
            return 2
        log(f"Render summary: {len(result['changed'])} changed, {len(result['unchanged'])} unchanged.")

    if args.verify or args.all:
        fragments = load_fragments()
        manifest = load_manifest()
        pages = resolve_pages(args.pages)
        checks = verify_site(pages, fragments, manifest["pages"])
        write_report("verify", checks)
        problems = not checks["idempotent"] or checks["manifest_drift"] or checks["structural"]
        if problems:
            log("VERIFY: issues found:")
            log(json.dumps(checks, indent=2)[:5000])
            return 1
        log("VERIFY: all checks passed.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
