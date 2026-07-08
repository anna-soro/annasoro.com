#!/usr/bin/env python3
"""Localize remote assets (images, CSS, hero video, CCV case-study video)
referenced by the top-level HTML files into dist/, and rewrite the HTML to
point at the local copies. See docs/plan/localize-remote-assets.md.

Usage:
    python3 scripts/localize_assets.py --inventory-only
    python3 scripts/localize_assets.py --download [--categories images,css,hero-video,ccv-video] [--refresh-ccv]
    python3 scripts/localize_assets.py --rewrite --dry-run
    python3 scripts/localize_assets.py --rewrite
    python3 scripts/localize_assets.py --verify
    python3 scripts/localize_assets.py --all
"""
from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = REPO_ROOT / "app"
HTML_FILES = sorted(APP_ROOT.glob("*.html"))
DIST_IMAGES = APP_ROOT / "dist" / "images"
DIST_CSS_PAGES = APP_ROOT / "dist" / "css" / "pages"
DIST_VIDEO = APP_ROOT / "dist" / "video"
DIST_VIDEO_CASE_STUDIES = DIST_VIDEO / "case-studies"
MANIFEST_PATH = REPO_ROOT / "scripts" / "asset_manifest.json"
INVENTORY_PATH = REPO_ROOT / "scripts" / "inventory.json"
REPORTS_DIR = REPO_ROOT / "scripts" / "reports"
BASELINE_PATH = REPORTS_DIR / "baseline_counts.json"

IMAGE_EXTS = ("jpg", "jpeg", "png", "gif", "svg", "webp")
OUT_OF_SCOPE_DOMAINS = {
    "typekit": r"use\.typekit\.net",
    "vimeo": r"player\.vimeo\.com",
    "behance": r"behance\.net",
    "linkedin": r"linkedin\.com",
    "telegram": r"t\.me/",
}

IMAGE_URL_RE = re.compile(
    r"https://cdn\.myportfolio\.com/[0-9a-f-]+/"
    r"(?P<basename>[^/?\"'\s]+\.(?:" + "|".join(IMAGE_EXTS) + r"))"
    r"\?h=[0-9a-f]+",
    re.IGNORECASE,
)
CSS_URL_RE = re.compile(
    r"https://cdn\.myportfolio\.com/[0-9a-f-]+/"
    r"(?P<basename>[0-9a-f]+\.css)"
    r"\?h=[0-9a-f]+"
)
HERO_BLOCK_RE = re.compile(
    r"<img\s+class=['\"]mp4-image['\"].*?/>\s*"
    r"<video\s+class=['\"]renditions-video['\"](?P<attrs>[^>]*)>"
    r"(?P<sources>.*?)"
    r"</video>",
    re.DOTALL,
)
HERO_ID_RE = re.compile(r"ccvproxy/(?P<id>[A-Za-z0-9_-]+)\?")
CCV_IFRAME_RE = re.compile(
    r"<iframe title=\"Video Player\" class=\"embed-content\" "
    r"src=\"https://www-ccv\.adobe\.io/v1/player/ccv/(?P<id>[A-Za-z0-9_-]+)/embed\?[^\"]*\" "
    r"frameborder=\"0\" allowfullscreen style=\"(?P<style>[^\"]*)\"></iframe>"
)
CCVPROXY_RE = re.compile(r"/v1/ccvproxy/")
DIST_REF_RE = re.compile(r'(?:src|href|data-src)="(dist/[^"]+)"')


class CCVParseError(Exception):
    pass


@dataclasses.dataclass
class AssetRef:
    category: str  # "image" | "css" | "hero-video" | "ccv-video"
    key: str  # manifest key: URL for image/css/hero-video, ccv id for ccv-video
    url: str  # fetch URL (image/css: direct; hero-video: chosen rendition; ccv-video: embed page URL)
    local_rel_path: str
    pages: dict = dataclasses.field(default_factory=dict)  # page_name -> occurrence count
    meta: dict = dataclasses.field(default_factory=dict)


def log(msg: str) -> None:
    print(msg, flush=True)


def _bump_page(ref: AssetRef, page: str) -> None:
    ref.pages[page] = ref.pages.get(page, 0) + 1


# ---------------------------------------------------------------------------
# Phase 1: inventory extraction
# ---------------------------------------------------------------------------

def pick_rendition_closest_to(renditions: list, target_width: int = 1280):
    """renditions: [(width:int, url:str), ...] mp4-only. Ties -> smaller width."""
    return min(renditions, key=lambda w_u: (abs(w_u[0] - target_width), w_u[0]))


def _add_image(url: str, page: str, out: dict) -> None:
    m = IMAGE_URL_RE.search(url)
    if not m or "/v1/ccvproxy/" in url:
        return
    key = url
    ref = out.get(key)
    if ref is None:
        basename = m.group("basename")
        ref = AssetRef("image", key, url, f"dist/images/{basename}")
        out[key] = ref
    _bump_page(ref, page)


def _extract_images(soup: BeautifulSoup, page: str, out: dict) -> None:
    for img in soup.find_all("img"):
        for attr in ("src", "data-src"):
            url = img.get(attr)
            if url:
                _add_image(url, page, out)
        for attr in ("srcset", "data-srcset"):
            srcset = img.get(attr)
            if srcset:
                for part in srcset.split(","):
                    url = part.strip().split(" ")[0].strip()
                    if url:
                        _add_image(url, page, out)
    for link in soup.find_all("link", rel=True):
        rels = link.get("rel")
        rels = rels if isinstance(rels, list) else [rels]
        if any("icon" in r for r in rels):
            href = link.get("href")
            if href:
                _add_image(href, page, out)
    for meta in soup.find_all("meta", property="og:image"):
        content = meta.get("content")
        if content:
            _add_image(content, page, out)
    for el in soup.find_all(class_="js-lightbox"):
        data_src = el.get("data-src")
        if data_src:
            _add_image(data_src, page, out)


def _extract_css(soup: BeautifulSoup, page: str, out: dict) -> None:
    for link in soup.find_all("link", rel="stylesheet"):
        href = link.get("href")
        if not href or "cdn.myportfolio.com" not in href:
            continue
        m = CSS_URL_RE.search(href)
        if not m:
            continue
        key = href
        ref = out.get(key)
        if ref is None:
            ref = AssetRef("css", key, href, f"dist/css/pages/{m.group('basename')}")
            out[key] = ref
        _bump_page(ref, page)


def _extract_hero_video(soup: BeautifulSoup, page: str, out: dict) -> None:
    for video in soup.find_all("video", class_="renditions-video"):
        renditions = []
        for source in video.find_all("source"):
            if source.get("type") != "video/mp4":
                continue
            url = source.get("data-src") or source.get("src")
            if not url:
                continue
            # BeautifulSoup decodes attribute entities, so url has "&" not "&amp;" here.
            m = re.search(r"ccvproxy/(?P<id>[A-Za-z0-9_-]+)\?width=(?P<width>\d+)&type=mp4", url)
            if not m:
                continue
            renditions.append((int(m.group("width")), url, m.group("id")))
        if not renditions:
            continue
        ccv_id = renditions[0][2]
        width, chosen_url = pick_rendition_closest_to([(w, u) for w, u, _ in renditions])
        key = ccv_id
        ref = out.get(key)
        if ref is None:
            ref = AssetRef(
                "hero-video", key, chosen_url,
                f"dist/video/{ccv_id}-{width}.mp4",
                meta={"width": width},
            )
            out[key] = ref
        _bump_page(ref, page)


def _extract_ccv_iframes(soup: BeautifulSoup, page: str, out: dict) -> None:
    for iframe in soup.find_all("iframe", class_="embed-content"):
        src = iframe.get("src") or ""
        if not src.startswith("https://www-ccv.adobe.io/v1/player/ccv/"):
            continue
        m = re.search(r"/v1/player/ccv/(?P<id>[A-Za-z0-9_-]+)/embed", src)
        if not m:
            continue
        ccv_id = m.group("id")
        key = ccv_id
        ref = out.get(key)
        if ref is None:
            ref = AssetRef(
                "ccv-video", key, src,
                f"dist/video/case-studies/{ccv_id}.mp4",
            )
            out[key] = ref
        _bump_page(ref, page)


def _iter_soups_including_templates(soup: BeautifulSoup):
    """Yield soup plus any documents nested inside <script type="text/html">
    template blocks (e.g. js-lightbox-slide-content), recursively — the
    parser treats script content as opaque text, so markup in there is
    otherwise invisible to find_all()."""
    stack = [soup]
    while stack:
        current = stack.pop()
        yield current
        for script in current.find_all("script", type="text/html"):
            inner_html = script.string or script.get_text()
            if inner_html and "cdn.myportfolio.com" in inner_html:
                stack.append(BeautifulSoup(inner_html, "html.parser"))


def extract_inventory(html_paths: list) -> dict:
    assets: dict = {}
    for path in html_paths:
        text = path.read_text(encoding="utf-8")
        soup = BeautifulSoup(text, "html.parser")
        page = path.name
        for s in _iter_soups_including_templates(soup):
            _extract_images(s, page, assets)
            _extract_css(s, page, assets)
            _extract_hero_video(s, page, assets)
            _extract_ccv_iframes(s, page, assets)
    return assets


def save_inventory(assets: dict, path: Path = INVENTORY_PATH) -> None:
    counts: dict = {}
    for ref in assets.values():
        counts[ref.category] = counts.get(ref.category, 0) + 1
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
        "assets": {
            key: {
                "category": ref.category,
                "url": ref.url,
                "local_rel_path": ref.local_rel_path,
                "pages": dict(sorted(ref.pages.items())),
                "meta": ref.meta,
            }
            for key, ref in assets.items()
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log(f"Inventory written to {path} — counts: {counts}")


def load_inventory(path: Path = INVENTORY_PATH) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    assets = {}
    for key, v in data["assets"].items():
        assets[key] = AssetRef(
            v["category"], key, v["url"], v["local_rel_path"], v["pages"], v.get("meta", {})
        )
    return assets


# ---------------------------------------------------------------------------
# Phase 2: download
# ---------------------------------------------------------------------------

def build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=5, backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": "Mozilla/5.0 (localize_assets.py; +internal-tooling)"})
    return s


def atomic_download(session: requests.Session, url: str, dest: Path, timeout: int = 30):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    size = 0
    with session.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if not chunk:
                    continue
                f.write(chunk)
                h.update(chunk)
                size += len(chunk)
    os.replace(tmp, dest)
    return h.hexdigest(), size


def _extract_balanced_braces(text: str, start_idx: int) -> str:
    depth = 0
    for i in range(start_idx, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start_idx : i + 1]
    raise CCVParseError("Unbalanced braces in ccv$serverData")


def parse_ccv_server_data(html_text: str) -> dict:
    marker = "ccv$serverData"
    idx = html_text.find(marker)
    if idx == -1:
        raise CCVParseError("ccv$serverData assignment not found")
    eq_idx = html_text.find("=", idx)
    brace_idx = html_text.find("{", eq_idx)
    if eq_idx == -1 or brace_idx == -1:
        raise CCVParseError("ccv$serverData object start not found")
    obj_text = _extract_balanced_braces(html_text, brace_idx)
    try:
        return json.loads(obj_text)
    except json.JSONDecodeError:
        mp4 = re.search(r'"mp4URL"\s*:\s*"([^"]+)"', obj_text)
        if not mp4:
            raise CCVParseError("mp4URL not found via fallback regex either")
        return {"mp4URL": mp4.group(1).replace("\\/", "/")}


def fetch_ccv_server_data(session: requests.Session, embed_url: str) -> dict:
    r = session.get(embed_url, timeout=20)
    r.raise_for_status()
    return parse_ccv_server_data(r.text)


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"images": {}, "css": {}, "hero_video": {}, "ccv_video": {}}


def save_manifest(manifest: dict, path: Path = MANIFEST_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


CATEGORY_TO_MANIFEST_KEY = {
    "image": "images",
    "css": "css",
    "hero-video": "hero_video",
    "ccv-video": "ccv_video",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def download_one(session: requests.Session, ref: AssetRef, manifest: dict, refresh_ccv: bool) -> dict:
    mkey = CATEGORY_TO_MANIFEST_KEY[ref.category]
    dest = APP_ROOT / ref.local_rel_path
    prior = manifest[mkey].get(ref.key)

    if ref.category != "ccv-video":
        if prior and prior.get("status") == "success" and dest.exists():
            return prior
        try:
            sha256, size = atomic_download(session, ref.url, dest)
            entry = {
                "local_path": ref.local_rel_path, "status": "success",
                "sha256": sha256, "size_bytes": size, "downloaded_at": _now(),
                "source_url": ref.url,
            }
        except Exception as e:  # noqa: BLE001
            entry = {"local_path": ref.local_rel_path, "status": "failed", "error": str(e), "downloaded_at": _now()}
        manifest[mkey][ref.key] = entry
        return entry

    # ccv-video: only re-fetch if missing/failed/forced
    if not refresh_ccv and prior and prior.get("status") == "success" and dest.exists():
        return prior
    try:
        data = fetch_ccv_server_data(session, ref.url)
        mp4_url = data.get("mp4URL")
        if not mp4_url:
            raise CCVParseError("mp4URL missing from ccv$serverData")
        sha256, size = atomic_download(session, mp4_url, dest)
        entry = {
            "local_path": ref.local_rel_path, "status": "success",
            "sha256": sha256, "size_bytes": size, "downloaded_at": _now(),
            "embed_url": ref.url,
        }
    except Exception as e:  # noqa: BLE001
        entry = {"local_path": ref.local_rel_path, "status": "failed", "error": str(e),
                  "downloaded_at": _now(), "embed_url": ref.url}
    manifest[mkey][ref.key] = entry
    return entry


def run_downloads(assets: dict, manifest: dict, categories: set, refresh_ccv: bool, concurrency: int) -> dict:
    session = build_session()
    todo = [ref for ref in assets.values() if ref.category in categories]
    ccv_todo = [ref for ref in todo if ref.category == "ccv-video"]
    other_todo = [ref for ref in todo if ref.category != "ccv-video"]

    results = {"attempted": 0, "succeeded": 0, "failed": 0}

    def run_pool(items, workers):
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(download_one, session, ref, manifest, refresh_ccv): ref for ref in items}
            for fut in concurrent.futures.as_completed(futures):
                ref = futures[fut]
                entry = fut.result()
                results["attempted"] += 1
                if entry.get("status") == "success":
                    results["succeeded"] += 1
                else:
                    results["failed"] += 1
                    log(f"  FAILED [{ref.category}] {ref.key}: {entry.get('error')}")

    if other_todo:
        log(f"Downloading {len(other_todo)} image/css/hero-video assets (concurrency={concurrency})...")
        run_pool(other_todo, concurrency)
    if ccv_todo:
        log(f"Downloading {len(ccv_todo)} ccv-video assets (concurrency={min(4, concurrency)})...")
        run_pool(ccv_todo, min(4, concurrency))

    return results


# ---------------------------------------------------------------------------
# Phase 3: rewrite
# ---------------------------------------------------------------------------

def build_hero_replacement(attrs: str, ccv_id: str, manifest: dict) -> Optional[str]:
    entry = manifest["hero_video"].get(ccv_id)
    if not entry or entry.get("status") != "success":
        return None
    cleaned = re.sub(r"\s*data-responsive-video\s*", " ", attrs).rstrip()
    local_path = entry["local_path"]
    return (
        f"<video class='renditions-video'{cleaned}>\n"
        f'      <source src="{local_path}" type="video/mp4">\n'
        f"</video>"
    )


def build_ccv_replacement(ccv_id: str, style: str, manifest: dict) -> Optional[str]:
    entry = manifest["ccv_video"].get(ccv_id)
    if not entry or entry.get("status") != "success":
        return None
    local_path = entry["local_path"]
    return f'<video controls class="embed-content" style="{style}" src="{local_path}"></video>'


def count_matches_for_text(text: str) -> dict:
    return {
        "image": len(IMAGE_URL_RE.findall(text)),
        "css": len(CSS_URL_RE.findall(text)),
        "hero-video": len(HERO_BLOCK_RE.findall(text)),
        "ccv-video": len(CCV_IFRAME_RE.findall(text)),
    }


def rewrite_file_text(text: str, manifest: dict, failures: list, page: str) -> str:
    def image_repl(m: re.Match) -> str:
        url = m.group(0)
        entry = manifest["images"].get(url)
        if entry and entry.get("status") == "success":
            return entry["local_path"]
        failures.append({"category": "image", "key": url, "page": page,
                          "reason": (entry or {}).get("error", "not downloaded")})
        return url

    def css_repl(m: re.Match) -> str:
        url = m.group(0)
        entry = manifest["css"].get(url)
        if entry and entry.get("status") == "success":
            return entry["local_path"]
        failures.append({"category": "css", "key": url, "page": page,
                          "reason": (entry or {}).get("error", "not downloaded")})
        return url

    def hero_repl(m: re.Match) -> str:
        id_match = HERO_ID_RE.search(m.group("sources"))
        ccv_id = id_match.group("id") if id_match else None
        replacement = build_hero_replacement(m.group("attrs"), ccv_id, manifest) if ccv_id else None
        if replacement is None:
            failures.append({"category": "hero-video", "key": ccv_id, "page": page,
                              "reason": (manifest["hero_video"].get(ccv_id) or {}).get("error", "not downloaded")})
            return m.group(0)
        return replacement

    def ccv_repl(m: re.Match) -> str:
        ccv_id = m.group("id")
        replacement = build_ccv_replacement(ccv_id, m.group("style"), manifest)
        if replacement is None:
            failures.append({"category": "ccv-video", "key": ccv_id, "page": page,
                              "reason": (manifest["ccv_video"].get(ccv_id) or {}).get("error", "not downloaded")})
            return m.group(0)
        return replacement

    text = HERO_BLOCK_RE.sub(hero_repl, text)
    text = CCV_IFRAME_RE.sub(ccv_repl, text)
    text = IMAGE_URL_RE.sub(image_repl, text)
    text = CSS_URL_RE.sub(css_repl, text)
    return text


def run_rewrite(html_paths: list, assets: dict, manifest: dict, dry_run: bool) -> dict:
    # self-check: per-file expected counts (from inventory pages) vs actual regex match counts
    expected_by_file: dict = {p.name: {"image": 0, "css": 0, "hero-video": 0, "ccv-video": 0} for p in html_paths}
    for ref in assets.values():
        for page, count in ref.pages.items():
            if page in expected_by_file:
                expected_by_file[page][ref.category] += count

    mismatches = []
    for path in html_paths:
        text = path.read_text(encoding="utf-8")
        actual = count_matches_for_text(text)
        expected = expected_by_file[path.name]
        for cat in expected:
            if actual[cat] != expected[cat]:
                mismatches.append({"file": path.name, "category": cat, "expected": expected[cat], "actual": actual[cat]})

    if mismatches:
        log("SELF-CHECK FAILED — regex match counts do not equal inventory counts. Aborting rewrite.")
        for m in mismatches:
            log(f"  {m['file']}: {m['category']} expected={m['expected']} actual={m['actual']}")
        return {"aborted": True, "mismatches": mismatches, "failures": []}

    log("Self-check passed: regex match counts equal inventory counts for all files.")

    failures: list = []
    if dry_run:
        log("Dry run: no files written.")
        return {"aborted": False, "mismatches": [], "failures": failures, "dry_run": True}

    for path in html_paths:
        text = path.read_text(encoding="utf-8")
        new_text = rewrite_file_text(text, manifest, failures, path.name)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")

    return {"aborted": False, "mismatches": [], "failures": failures, "dry_run": False}


# ---------------------------------------------------------------------------
# Phase 4: verify
# ---------------------------------------------------------------------------

def capture_baseline_counts(html_paths: list) -> dict:
    counts = {}
    for label, pattern in OUT_OF_SCOPE_DOMAINS.items():
        counts[label] = sum(len(re.findall(pattern, p.read_text(encoding="utf-8"))) for p in html_paths)
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(counts, indent=2), encoding="utf-8")
    return counts


def verify_site(html_paths: list) -> dict:
    text_by_file = {p: p.read_text(encoding="utf-8") for p in html_paths}
    checks: dict = {}

    checks["remaining_image_css"] = {
        p.name: len(IMAGE_URL_RE.findall(t)) + len(CSS_URL_RE.findall(t))
        for p, t in text_by_file.items() if IMAGE_URL_RE.search(t) or CSS_URL_RE.search(t)
    }
    checks["remaining_ccvproxy"] = {
        p.name: len(CCVPROXY_RE.findall(t)) for p, t in text_by_file.items() if CCVPROXY_RE.search(t)
    }
    checks["remaining_ccv_iframe"] = {
        p.name: len(CCV_IFRAME_RE.findall(t)) for p, t in text_by_file.items() if CCV_IFRAME_RE.search(t)
    }

    current_counts = {}
    for label, pattern in OUT_OF_SCOPE_DOMAINS.items():
        current_counts[label] = sum(len(re.findall(pattern, t)) for t in text_by_file.values())
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8")) if BASELINE_PATH.exists() else None
    checks["out_of_scope_counts"] = current_counts
    checks["out_of_scope_baseline"] = baseline
    checks["out_of_scope_drift"] = (
        {k: (baseline[k], current_counts[k]) for k in current_counts if baseline and baseline.get(k) != current_counts[k]}
        if baseline else None
    )

    all_text = "\n".join(text_by_file.values())
    referenced = set(DIST_REF_RE.findall(all_text))
    missing = sorted(p for p in referenced if not (APP_ROOT / p).exists())
    checks["missing_dist_files"] = missing

    return checks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def write_report(phase: str, extra: dict) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = REPORTS_DIR / f"run_{ts}_{phase}.json"
    payload = {"phase": phase, "finished_at": _now(), **extra}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log(f"Report written to {path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--rewrite", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--refresh-ccv", action="store_true")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--categories", type=str, default="image,css,hero-video,ccv-video")
    args = parser.parse_args()

    if not any([args.inventory_only, args.download, args.rewrite, args.verify, args.all]):
        parser.print_help()
        return 2

    categories = set(args.categories.split(","))

    if args.inventory_only or args.all:
        log(f"Scanning {len(HTML_FILES)} HTML files...")
        assets = extract_inventory(HTML_FILES)
        save_inventory(assets)
        if not BASELINE_PATH.exists():
            baseline = capture_baseline_counts(HTML_FILES)
            log(f"Baseline out-of-scope counts captured: {baseline}")

    if args.download or args.all:
        assets = load_inventory() if INVENTORY_PATH.exists() else extract_inventory(HTML_FILES)
        if not INVENTORY_PATH.exists():
            save_inventory(assets)
        manifest = load_manifest()
        results = run_downloads(assets, manifest, categories, args.refresh_ccv, args.concurrency)
        save_manifest(manifest)
        log(f"Download summary: {results}")
        write_report("download", {"summary": results})

    if args.rewrite or args.all:
        assets = load_inventory()
        manifest = load_manifest()
        result = run_rewrite(HTML_FILES, assets, manifest, dry_run=args.dry_run)
        write_report("rewrite", result)
        if result["aborted"]:
            return 2
        if result["failures"]:
            log(f"Rewrite completed with {len(result['failures'])} unresolved references left pointing at remote URLs.")

    if args.verify or args.all:
        checks = verify_site(HTML_FILES)
        write_report("verify", checks)
        problems = (
            checks["remaining_image_css"] or checks["remaining_ccvproxy"] or
            checks["remaining_ccv_iframe"] or checks["out_of_scope_drift"] or checks["missing_dist_files"]
        )
        if problems:
            log("VERIFY: issues found:")
            log(json.dumps(checks, indent=2))
            return 1
        log("VERIFY: all checks passed.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
