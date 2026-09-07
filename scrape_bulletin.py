import json
import re
import time

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Bulletin course-search page that exposes the department prefix dropdown.
SEARCH_URL = "https://www.stonybrook.edu/sb/bulletin/current-fall24/search/"
BASE_URL = (
    "https://www.stonybrook.edu/sb/bulletin/current-fall24/"
    "academicprograms/{}/courses.php"
)

DEPT_CODE_RE = re.compile(r"^[A-Za-z]{3}$")


def get_departments() -> list[str]:
    """Fetch all 3-letter department codes from the bulletin course search page."""
    print(f"Fetching department list from {SEARCH_URL}...")
    response = requests.get(SEARCH_URL, headers=HEADERS, timeout=60)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # Prefer name="subject" if present; SBU currently uses name="abbreviation".
    select = soup.find("select", attrs={"name": "subject"})
    if select is None:
        select = soup.find("select", attrs={"name": "abbreviation"})
    if select is None:
        raise RuntimeError(
            "Could not find department <select name='subject'> or "
            "<select name='abbreviation'> on the course search page."
        )

    departments: list[str] = []
    seen: set[str] = set()
    for option in select.find_all("option"):
        value = (option.get("value") or "").strip()
        if not value:
            continue
        # Exclude blanks, wildcards (*, %), and non 3-letter codes.
        if value in {"*", "%", "all", "ALL"}:
            continue
        if not DEPT_CODE_RE.fullmatch(value):
            continue
        code = value.lower()
        if code not in seen:
            seen.add(code)
            departments.append(code)

    if not departments:
        raise RuntimeError("No 3-letter department codes found in the dropdown.")

    print(f"Found {len(departments)} departments.")
    return departments


def scrape_bulletin():
    departments = get_departments()
    all_courses = {}

    for dept in departments:
        print(f"Scraping {dept.upper()}...")
        url = BASE_URL.format(dept)

        response = requests.get(url, headers=HEADERS, timeout=60)
        if response.status_code != 200:
            print(f"Failed to fetch {dept}. Status code: {response.status_code}")
            continue

        soup = BeautifulSoup(response.text, "html.parser")

        # SBU courses are typically grouped under standard div classes in the bulletin
        course_blocks = soup.find_all("div", class_="course")

        for block in course_blocks:
            try:
                # 1. Get Course Title (e.g., "CSE 214: Data Structures")
                title_tag = block.find("h3")
                if not title_tag:
                    continue
                full_title = title_tag.text.strip()

                # 2. Get the actual course code (e.g., "CSE 214")
                code_match = re.match(r"([A-Z]{3}\s*\d{3})", full_title)
                course_code = code_match.group(1) if code_match else full_title

                # 3. Extract all paragraph text
                paragraphs = block.find_all("p")
                description = paragraphs[0].text.strip() if paragraphs else ""

                # 4. Find prerequisites and credits using string matching
                prereqs = ""
                credits = ""
                sbcs = []

                for p in paragraphs:
                    text = p.text.strip()
                    if text.startswith("Prerequisite"):
                        prereqs = text
                    elif "credits" in text.lower():
                        credits = text

                # 5. Find SBCs (Stony Brook Curriculum) usually in anchor tags
                sbc_tags = block.find_all("a", title=True)
                for tag in sbc_tags:
                    if tag.text.isupper() and len(tag.text) <= 4:
                        sbcs.append(tag.text)

                all_courses[course_code] = {
                    "full_title": full_title,
                    "department": dept.upper(),
                    "description": description,
                    "prerequisites": prereqs,
                    "sbcs": list(set(sbcs)),
                    "credits": credits,
                }

            except Exception as e:
                print(f"Error parsing a course: {e}")

        # Small delay to prevent rate-limiting
        time.sleep(1)

    # Save output to JSON
    with open("sbu_courses.json", "w", encoding="utf-8") as f:
        json.dump(all_courses, f, indent=4)

    print(f"Done! Scraped {len(all_courses)} courses into sbu_courses.json.")


if __name__ == "__main__":
    scrape_bulletin()
