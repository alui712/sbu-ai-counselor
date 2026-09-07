"""Scrape Stony Brook Undergraduate Catalog majors and minors.

Uses the live Acalog catalog (catalog.stonybrook.edu). AWS WAF protects
content pages, so Playwright is used to pass the challenge once; program
detail pages are then fetched with the authenticated browser context.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

CATALOG_BASE = "https://catalog.stonybrook.edu/"
# Fall 2026 Undergraduate Catalog
CATOID = 11
MAJORS_NAVOID = 1134
MINORS_NAVOID = 1136

DEGREE_SUFFIX_RE = re.compile(
    r",\s*(BA|BS|BE|BFA|BM|BMus)\b",
    re.IGNORECASE,
)
OUTPUT_PATH = Path(__file__).resolve().parent / "sbu_programs.json"


def _display_name(raw_name: str, program_type: str) -> str:
    """Normalize catalog labels to the names the app expects."""
    name = re.sub(r"\s+", " ", (raw_name or "").replace("\xa0", " ")).strip()
    # Catalog uses a curly apostrophe in Women’s …
    name = name.replace("\u2019", "'").replace("\u2018", "'")
    if program_type == "Minor" and not re.search(r"\bminor\b", name, re.I):
        name = f"{name} Minor"
    return name


def _wait_for_catalog(page, needle: str = "preview_program.php", timeout_s: float = 60) -> None:
    deadline = time.time() + timeout_s
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5_000)
            html = page.content()
        except Exception as exc:  # noqa: BLE001 — navigation race during WAF
            last_err = exc
            time.sleep(0.5)
            continue
        if "challenge-container" not in html and needle in html:
            return
        time.sleep(0.4)
    raise TimeoutError(
        f"Timed out waiting for catalog page (WAF or empty content). Last error: {last_err}"
    )


def _collect_index(page, navoid: int, program_type: str) -> list[dict]:
    url = f"{CATALOG_BASE}content.php?catoid={CATOID}&navoid={navoid}"
    print(f"Fetching {program_type} index: {url}")
    for attempt in range(3):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=120_000)
            _wait_for_catalog(page)
            break
        except Exception as exc:  # noqa: BLE001
            print(f"  retry {attempt + 1}/3 after: {exc}")
            time.sleep(1.5)
    else:
        raise TimeoutError(f"Could not load catalog index for {program_type}")

    # Prefer waiting on a real program link once WAF clears.
    try:
        page.wait_for_selector("a[href*='preview_program.php']", timeout=15_000)
    except Exception:  # noqa: BLE001
        pass

    soup = BeautifulSoup(page.content(), "html.parser")
    items: list[dict] = []
    seen_hrefs: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "preview_program.php" not in href:
            continue
        raw = anchor.get_text(" ", strip=True)
        if not raw:
            continue
        # Majors page includes non-degree entries (e.g. teacher certification).
        if program_type == "Major" and not DEGREE_SUFFIX_RE.search(raw):
            continue

        full_url = urljoin(CATALOG_BASE, href)
        if full_url in seen_hrefs:
            continue
        seen_hrefs.add(full_url)
        items.append(
            {
                "program_name": _display_name(raw, program_type),
                "type": program_type,
                "catalog_url": full_url,
                "raw_name": raw,
            }
        )

    print(f"  Found {len(items)} {program_type.lower()} programs.")
    return items


def _clean_requirements_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("td.block_content") or soup.select_one(".block_content")
    if main is None:
        main = soup.body
    if main is None:
        return ""

    for selector in ["nav", "header", "footer", "script", "style", ".noprint"]:
        for node in main.select(selector):
            node.decompose()

    text = main.get_text("\n", strip=True)
    text = text.replace("\xa0", " ")
    # Drop leading chrome / print toolbar noise.
    cut = re.search(
        r"(?:^|\n)(Degree Awarded:|Department of |College of |School of |"
        r"Requirements for the |Admission Requirements|Acceptance into the |"
        r"Minor Requirements|Major Requirements)",
        text,
        flags=re.IGNORECASE,
    )
    if cut and cut.start(1) < 1200:
        text = text[cut.start(1) :]
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def scrape_programs() -> list[dict]:
    """Index all majors/minors and scrape their requirements text."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "Playwright is required to scrape the current catalog.\n"
            "  pip install playwright && python -m playwright install chromium"
        ) from exc

    results: list[dict] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()
        # Warm the session / pass WAF on the catalog home page.
        page.goto(CATALOG_BASE, wait_until="domcontentloaded", timeout=120_000)
        time.sleep(1.5)

        index = _collect_index(page, MAJORS_NAVOID, "Major")
        index += _collect_index(page, MINORS_NAVOID, "Minor")

        for i, entry in enumerate(index, start=1):
            name = entry["program_name"]
            program_type = entry["type"]
            url = entry["catalog_url"]
            print(f"[{i}/{len(index)}] {program_type}: {name}")

            try:
                response = context.request.get(url, timeout=60_000)
                html = response.text() if response.ok else ""
            except Exception as exc:  # noqa: BLE001
                print(f"  ! fetch failed: {exc}")
                html = ""

            requirements = _clean_requirements_html(html) if html else ""
            if len(requirements) < 80:
                print(f"  ! thin requirements ({len(requirements)} chars)")

            results.append(
                {
                    "program_name": name,
                    "type": program_type,
                    "requirements_text": requirements,
                    "catalog_url": url,
                }
            )
            time.sleep(0.15)

        browser.close()

    OUTPUT_PATH.write_text(
        json.dumps(results, indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    majors = sum(1 for r in results if r["type"] == "Major")
    minors = sum(1 for r in results if r["type"] == "Minor")
    print(
        f"Done! Saved {len(results)} programs "
        f"({majors} majors, {minors} minors) to {OUTPUT_PATH.name}."
    )
    return results


if __name__ == "__main__":
    scrape_programs()
