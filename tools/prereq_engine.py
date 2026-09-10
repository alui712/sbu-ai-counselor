"""Prerequisite DAG engine for SBU courses, exposed as LangChain tools."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

import networkx as nx
from langchain_core.tools import tool

COURSE_RE = re.compile(r"([A-Z]{3}\s*\d{3})")
# Full codes or bare numbers (e.g. "MAT 125 or 131").
COURSE_TOKEN_RE = re.compile(r"([A-Z]{3})\s*(\d{3})|(\d{3})", re.IGNORECASE)
MATH_PLACEMENT_RE = re.compile(
    r"level\s*\d+\s+on\s+the\s+mathematics\s+placement|mathematics\s+placement\s+exam",
    re.IGNORECASE,
)
MATH_PLACEMENT_TOKEN = "__MATH_PLACEMENT__"
STANDING_RE = re.compile(
    r"\bU([2-4])\s+standing\b|\b(sophomore|junior|senior)\s+standing\b",
    re.IGNORECASE,
)
SBC_OR_DEC_PREREQ_RE = re.compile(
    r"(?:one\s+)?(?:D\.?E\.?C\.?\s*[EABCD]|SNW|ARTS|HUM|SBS|TECH|STAS|USA|GLO|DIV|CER|ESI)\b"
    r".{0,40}\bcourse\b|\bcourse\b.{0,40}(?:D\.?E\.?C\.?\s*[EABCD]|SNW)\b",
    re.IGNORECASE,
)
DEC_E_OR_SNW_RE = re.compile(r"D\.?E\.?C\.?\s*E|\bSNW\b", re.IGNORECASE)
MAJOR_RESTRICT_RE = re.compile(
    r"\b([A-Z]{2,4}(?:,\s*[A-Z]{2,4})*(?:\s+or\s+[A-Z]{2,4})?)\s+major\b",
    re.IGNORECASE,
)
PERMISSION_RE = re.compile(
    r"permission of (?:the )?(?:instructor|department|director)|consent of",
    re.IGNORECASE,
)
EXCLUSION_CONTEXT_RE = re.compile(
    r"(?i)(cannot be used|may not be used|does not (?:count|satisfy)|"
    r"do not satisfy|not satisfy|excluding|except\b|e\.g\.|for example|"
    r"such as|including but not)",
)
# First-semester calculus gateways that placement exams commonly unlock.
CALC_I_GATEWAYS = {"AMS 151", "MAT 125", "MAT 131", "MAT 141"}
PREP_MATH_COURSES = {"MAT 123", "MAP 103", "MAT 119", "MAT 122"}
# Mutually exclusive calculus sequences in SBU STEM majors.
CALC_TRACKS: list[tuple[str, ...]] = [
    ("AMS 151", "AMS 161"),
    ("MAT 131", "MAT 132"),
    ("MAT 125", "MAT 126", "MAT 127"),
    ("MAT 141", "MAT 142"),
]
CALC_TRACK_SETS: list[set[str]] = [set(track) for track in CALC_TRACKS]
# Named dept codes that appear in "X major" restrictions.
KNOWN_MAJOR_DEPTS = {
    "CSE",
    "ISE",
    "DAS",
    "AMS",
    "BIO",
    "CHE",
    "PHY",
    "MAT",
    "MEC",
    "ESE",
    "BME",
    "CIV",
    "CME",
    "BUS",
    "ACC",
    "PSY",
    "SOC",
    "POL",
    "ECO",
    "TSM",
}
DATA_PATH = Path(__file__).resolve().parent.parent / "sbu_courses.json"
PROGRAMS_PATH = Path(__file__).resolve().parent.parent / "sbu_programs.json"


def _normalize_code(code: str) -> str:
    """Normalize course codes to 'DEPT ###' with a single regular space.

    Accepts fuzzy inputs like 'CSE214', 'cse 214', or 'CSE  214' and returns
    'CSE 214'.
    """
    cleaned = code.replace("\xa0", " ").strip().upper()
    # Insert a single space between the department letters and course number.
    cleaned = re.sub(r"([A-Z]+)\s*(\d+)", r"\1 \2", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _load_courses(courses_path: Path = DATA_PATH) -> dict[str, dict]:
    """Load sbu_courses.json keyed by normalized course codes."""
    with open(courses_path, encoding="utf-8") as f:
        raw_courses = json.load(f)

    courses: dict[str, dict] = {}
    for raw_code, info in raw_courses.items():
        courses[_normalize_code(raw_code)] = info
    return courses


def _extract_prereqs(prereq_text: str) -> list[str]:
    """Extract course codes from a prerequisites string via regex."""
    if not prereq_text:
        return []
    return [_normalize_code(m) for m in COURSE_RE.findall(prereq_text)]


def _parse_prereq_clauses(prereq_text: str) -> list[list[str]]:
    """Parse prerequisites into AND-of-OR clause groups.

    Example: "AMS 151 or MAT 125 or MAT 131" -> [["AMS 151", "MAT 125", "MAT 131"]]
    Example: "CSE 216 or CSE 260; CSE 220" -> [["CSE 216", "CSE 260"], ["CSE 220"]]

    Bare numbers after a department (e.g. "MAT 125 or 131") inherit that department.
    Math placement-exam alternatives are kept as MATH_PLACEMENT_TOKEN so roadmap
    logic can treat entry Calc I as reachable without inventing a fake course.
    A clause is satisfied if the student completed at least one course in the OR group.
    All clauses must be satisfied (logical AND).
    """
    if not prereq_text:
        return []

    # Conjunctions separate independent requirements.
    and_parts = re.split(r";|\band\b", prereq_text, flags=re.IGNORECASE)
    clauses: list[list[str]] = []

    for part in and_parts:
        part = part.strip()
        if not part:
            continue

        or_parts = re.split(r"\bor\b", part, flags=re.IGNORECASE)
        group: list[str] = []
        seen: set[str] = set()
        last_dept: str | None = None

        for alt in or_parts:
            for match in COURSE_TOKEN_RE.finditer(alt.upper()):
                if match.group(1) and match.group(2):
                    last_dept = match.group(1)
                    code = _normalize_code(f"{match.group(1)} {match.group(2)}")
                elif match.group(3) and last_dept:
                    code = _normalize_code(f"{last_dept} {match.group(3)}")
                else:
                    continue
                if code not in seen:
                    seen.add(code)
                    group.append(code)

        # Preserve placement-exam alternatives that course-regex otherwise drops.
        if MATH_PLACEMENT_RE.search(part) and MATH_PLACEMENT_TOKEN not in seen:
            group.append(MATH_PLACEMENT_TOKEN)
            seen.add(MATH_PLACEMENT_TOKEN)

        if group:
            clauses.append(group)

    return clauses


def _prereqs_satisfied(clauses: list[list[str]], completed: set[str]) -> bool:
    """Return True if every OR-group has at least one completed course.

    Placement-exam tokens never count here — strict catalog eligibility.
    """
    if not clauses:
        return True
    return all(
        any(course in completed for course in group if not str(course).startswith("__"))
        for group in clauses
    )


def _progress_prereqs_satisfied(
    course_code: str,
    completed: set[str],
    *,
    majors: list[str] | None = None,
) -> bool:
    """Eligibility for major-roadmap recommendations.

    Entry Calc I courses (AMS 151 / MAT 131 / MAT 125 / MAT 141) may be
    recommended when the only barrier is a math placement exam or prep math
    (MAT 123). Calc II and later still require a real completed prereq course.

    Standing, major-restriction, and SBC/DEC prerequisites are treated as hard
    blockers (unlike bare course-code OR groups).
    """
    code = _normalize_code(course_code)
    info = COURSES.get(code) or {}
    text = info.get("prerequisites") or ""
    if not _hard_barriers_clear(text, completed, majors=majors):
        return False

    clauses = _parse_prereq_clauses(text)
    if not clauses:
        return True

    is_calc_i = code in CALC_I_GATEWAYS
    for group in clauses:
        course_opts = [c for c in group if not str(c).startswith("__")]
        has_placement = any(str(c).startswith("__") for c in group)
        if any(c in completed for c in course_opts):
            continue
        # Allow recommending Calc I when placement or only prep-math stands in the way.
        if is_calc_i and (
            has_placement or (course_opts and set(course_opts) <= PREP_MATH_COURSES)
        ):
            continue
        return False
    return True


def _calc_track_index(course_code: str) -> int | None:
    code = _normalize_code(course_code)
    for idx, track in enumerate(CALC_TRACK_SETS):
        if code in track:
            return idx
    return None


def _preferred_calc_track(
    required: list[str], completed: set[str]
) -> int | None:
    """Pick one calculus sequence: continue a started track, else earliest in bulletin."""
    for idx, track in enumerate(CALC_TRACK_SETS):
        if completed & track:
            return idx
    for code in required:
        if code in completed:
            continue
        idx = _calc_track_index(code)
        if idx is not None:
            return idx
    return None


def _is_calc_sequence_group(group: set[str]) -> bool:
    """True when a bulletin menu is a calc sequence (or union of sequences), not a flat one-of."""
    codes = {_normalize_code(c) for c in group}
    for track in CALC_TRACK_SETS:
        if len(codes & track) >= 2:
            return True
    calc_all = set().union(*CALC_TRACK_SETS) if CALC_TRACK_SETS else set()
    return len(codes & calc_all) >= 4


def _estimate_class_standing(completed: set[str]) -> int:
    """Rough U1–U4 estimate from completed coursework (no credit transcript)."""
    if not completed:
        return 1
    nums = []
    for code in completed:
        try:
            nums.append(int(_normalize_code(code).split()[1]))
        except (IndexError, ValueError):
            continue
    n = len(completed)
    best = max(nums) if nums else 0
    if n >= 10 or best >= 300:
        return 4
    if n >= 6 or best >= 200:
        return 3
    if n >= 2 or best >= 120:
        return 2
    return 1


def _declared_major_depts(majors: list[str] | None) -> set[str]:
    """Map declared major names to likely department codes."""
    depts: set[str] = set()
    for major in majors or []:
        text = str(major or "").upper()
        for dept in KNOWN_MAJOR_DEPTS:
            if dept in text:
                depts.add(dept)
        # Common name → dept mappings when the code isn't in the title.
        if "COMPUTER SCIENCE" in text:
            depts.add("CSE")
        if "DATA SCIENCE" in text:
            depts.update({"DAS", "CSE", "AMS"})
        if "INFORMATION SYSTEMS" in text:
            depts.add("ISE")
        if "APPLIED MATHEMATICS" in text or "STATISTICS" in text:
            depts.add("AMS")
        if "TECHNOLOGICAL SYSTEMS" in text:
            depts.add("TSM")
        if "BIOLOGY" in text:
            depts.add("BIO")
        if "CHEMISTRY" in text:
            depts.add("CHE")
        if "PSYCHOLOGY" in text:
            depts.add("PSY")
        if "SOCIOLOGY" in text:
            depts.add("SOC")
        if "BUSINESS" in text or "ACCOUNTING" in text:
            depts.update({"BUS", "ACC"})
    return depts


def _completed_sbc_tags(completed: set[str]) -> set[str]:
    tags: set[str] = set()
    for code in completed:
        info = COURSES.get(_normalize_code(code)) or {}
        for tag in info.get("sbcs") or []:
            tags.add(str(tag).strip().upper())
    return tags


def _hard_barriers_clear(
    prereq_text: str,
    completed: set[str],
    *,
    majors: list[str] | None = None,
) -> bool:
    """Reject courses blocked by standing, major, or SBC/DEC prerequisites."""
    text = prereq_text or ""
    if not text.strip():
        return True

    if PERMISSION_RE.search(text):
        return False

    standing = _estimate_class_standing(completed)
    for match in STANDING_RE.finditer(text):
        token = (match.group(1) or match.group(2) or "").lower()
        need = {"2": 2, "sophomore": 2, "3": 3, "junior": 3, "4": 4, "senior": 4}.get(
            token, 1
        )
        if standing < need:
            return False

    # "CSE, ISE or DAS major" style restrictions.
    for match in MAJOR_RESTRICT_RE.finditer(text):
        raw = match.group(1).upper()
        needed = {
            p.strip()
            for p in re.split(r",|\bor\b", raw)
            if p.strip() and p.strip() in KNOWN_MAJOR_DEPTS
        }
        if needed and not (needed & _declared_major_depts(majors)):
            # If the student declared no majors at all, be conservative.
            if not majors:
                return False
            return False

    if DEC_E_OR_SNW_RE.search(text) and SBC_OR_DEC_PREREQ_RE.search(text):
        have = _completed_sbc_tags(completed)
        if "SNW" not in have:
            # DEC E maps roughly onto natural-science / SNW coursework.
            return False

    return True


def _has_college_calculus(completed: set[str]) -> bool:
    """True if the student already completed Calc I (or higher) in any SBU track."""
    have = {_normalize_code(c) for c in completed}
    if have & CALC_I_GATEWAYS:
        return True
    return any(have & track for track in CALC_TRACK_SETS)


def _is_superseded_by_progress(course_code: str, completed: set[str]) -> bool:
    """Skip intro/survey courses once the student is clearly past them."""
    code = _normalize_code(course_code)
    have = {_normalize_code(c) for c in completed}

    # Accounting / business bulletins: MAT 122 or MAT 123 "or a higher level calculus
    # course" — AMS 151 / MAT 125 / MAT 131 / etc. already finish that requirement.
    if code in PREP_MATH_COURSES and _has_college_calculus(have):
        return True

    if " " not in code:
        return False
    dept, num_s = code.split()[0], code.split()[1]
    try:
        num = int(num_s)
    except ValueError:
        return False

    same_dept = [
        c for c in have if c.startswith(dept + " ") and c != code
    ]
    if not same_dept:
        return False

    higher_nums = []
    for c in same_dept:
        try:
            higher_nums.append(int(c.split()[1]))
        except ValueError:
            continue
    if not higher_nums:
        return False
    best = max(higher_nums)

    # CSE 101 is obsolete once CSE 114+ is done.
    if code == "CSE 101" and best >= 114:
        return True
    # Generic: very low intro numbers once a substantially higher course is done.
    if num <= 110 and best >= max(num + 10, 114):
        return True
    if num < 200 and best >= 300:
        return True
    return False


def _missing_prereq_groups(
    clauses: list[list[str]], completed: set[str]
) -> list[str]:
    """Describe unsatisfied OR-groups for eligibility reporting."""
    missing: list[str] = []
    for group in clauses:
        if not any(course in completed for course in group):
            missing.append(" or ".join(group))
    return missing


def _build_prereq_graph(courses: dict[str, dict]) -> nx.DiGraph:
    """Build a DAG from a course dictionary: prereq -> course."""
    graph = nx.DiGraph()

    for course, info in courses.items():
        graph.add_node(course)

        for prereq in _extract_prereqs(info.get("prerequisites", "")):
            if prereq == course:
                continue
            graph.add_node(prereq)
            # Skip edges that would introduce a cycle so the graph stays a DAG.
            if graph.has_edge(prereq, course):
                continue
            graph.add_edge(prereq, course)
            if not nx.is_directed_acyclic_graph(graph):
                graph.remove_edge(prereq, course)

    return graph


def _build_prereq_clauses(courses: dict[str, dict]) -> dict[str, list[list[str]]]:
    """Map each course to its parsed AND-of-OR prerequisite clauses."""
    return {
        code: _parse_prereq_clauses(info.get("prerequisites", ""))
        for code, info in courses.items()
    }


COURSES: dict[str, dict] = _load_courses()
PREREQ_GRAPH: nx.DiGraph = _build_prereq_graph(COURSES)
PREREQ_CLAUSES: dict[str, list[list[str]]] = _build_prereq_clauses(COURSES)

CREDIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*credits?", re.IGNORECASE)
COURSE_MENTION_RE = re.compile(r"\b([A-Za-z]{3})\s*(\d{3})\b")
SPECIAL_COURSE_RE = re.compile(
    r"\b(undergraduate research|directed research|internship|teaching practicum|"
    r"senior project|honors project|independent study|thesis|"
    r"directed readings|senior seminar|special topics|speak effectively)\b",
    re.IGNORECASE,
)


def extract_program_course_mentions(program_name: str) -> dict | None:
    """Extract ordered unique catalog courses mentioned in a program's requirements.

    Returns None if the program cannot be matched. Course order follows first
    appearance in the bulletin text, which usually lists core requirements
    before long elective menus. Mentions inside exclusion / example prose are
    skipped so courses like CSE 301 (listed as non-technical) are not treated
    as requirements.
    """
    match = _fuzzy_find_program(program_name)
    if match is None:
        return None

    text = match.get("requirements_text") or ""
    # Footnote blocks are usually titled "Notes:" (plural). Do not treat a lone
    # early "Note:" (e.g. Accounting intro blurb) as the end of requirements.
    notes_at = re.search(r"(?m)^Notes:\s*$", text)
    notes_start = notes_at.start() if notes_at else len(text)
    ordered: list[str] = []
    positions: dict[str, int] = {}
    for match_obj in COURSE_MENTION_RE.finditer(text):
        code = _normalize_code(f"{match_obj.group(1)} {match_obj.group(2)}")
        if code not in COURSES or code in positions:
            continue
        start, end = match_obj.start(), match_obj.end()
        # Footnote-only mentions (e.g. CHE 301 prereq math options) are not cores.
        if start >= notes_start:
            continue
        window = text[max(0, start - 140) : min(len(text), end + 90)]
        if EXCLUSION_CONTEXT_RE.search(window):
            continue
        positions[code] = len(ordered)
        ordered.append(code)

    return {
        "program_name": match.get("program_name"),
        "program_type": match.get("type"),
        "courses": ordered,
        "positions": positions,
        "requirements_text": text,
    }


def extract_elective_menu_codes(requirements_text: str) -> set[str]:
    """Courses listed only as advisor electives / pick-from lists, not hard cores."""
    text = requirements_text or ""
    match = re.search(
        r"(?is)("
        r"chosen after consultation|"
        r"elective credits?[^\n]{0,120}following|"
        r"upper-?division electives?"
        r")",
        text,
    )
    if not match:
        return set()
    blob = text[match.start() :]
    # Stop before Notes / sample plans so core courses mentioned later aren't marked elective.
    cut = re.search(r"(?m)^(Notes:|Sample Course|Honors Program|BCB\b)", blob)
    if cut and cut.start() > 40:
        blob = blob[: cut.start()]
    return set(_extract_codes_from_blob(blob))


def extract_or_alternative_groups(requirements_text: str) -> list[set[str]]:
    """Parse bulletin lines linked by a standalone OR into either/or menus.

    Handles patterns like:
      BIO 205 - ...
      OR
      BIO 207 - ...
    and longer chains (BIO 320 OR BIO 321 OR EBH 302), including intervening
    '3 credits' / note lines between a course and its OR.
    """
    lines = (requirements_text or "").splitlines()
    parsed: list[tuple[str, list[str]]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            parsed.append(("skip", []))
            continue
        if re.fullmatch(r"OR", stripped, re.I):
            parsed.append(("or", []))
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?\s*credits?", stripped, re.I):
            parsed.append(("skip", []))
            continue
        if re.match(r"(?i)^(see note|note:|\()", stripped):
            parsed.append(("skip", []))
            continue
        codes: list[str] = []
        for match_obj in COURSE_MENTION_RE.finditer(stripped):
            code = _normalize_code(f"{match_obj.group(1)} {match_obj.group(2)}")
            if code in COURSES and code not in codes:
                codes.append(code)
        if codes and (
            re.search(r"[A-Z]{3}\s*\d{3}\s*[-–:]", stripped)
            or (len(codes) == 1 and len(stripped) < 80)
        ):
            parsed.append(("codes", codes))
        else:
            parsed.append(("other", codes))

    def next_meaningful(start: int) -> int:
        k = start
        while k < len(parsed) and parsed[k][0] == "skip":
            k += 1
        return k

    groups: list[set[str]] = []
    i = 0
    while i < len(parsed):
        kind, codes = parsed[i]
        if kind != "codes" or len(codes) != 1:
            i += 1
            continue
        chain = [codes[0]]
        j = next_meaningful(i + 1)
        while j < len(parsed):
            if parsed[j][0] != "or":
                break
            nxt = next_meaningful(j + 1)
            if nxt >= len(parsed):
                break
            nxt_kind, nxt_codes = parsed[nxt]
            if nxt_kind != "codes" or len(nxt_codes) != 1:
                break
            chain.append(nxt_codes[0])
            j = next_meaningful(nxt + 1)
        if len(chain) >= 2:
            group = set(chain)
            if not _is_calc_sequence_group(group) and not any(
                _calc_track_index(code) is not None for code in group
            ):
                if not any(
                    _one_is_direct_prereq_of_other(a, b)
                    for a in group
                    for b in group
                    if a != b
                ):
                    groups.append(group)
            i = j if j > i else i + 1
            continue
        i += 1
    return groups


def _one_is_direct_prereq_of_other(a: str, b: str) -> bool:
    """True when one course appears in the other's parsed prerequisite clauses."""
    for src, dst in ((a, b), (b, a)):
        clauses = PREREQ_CLAUSES.get(dst) or []
        flat = {course for group in clauses for course in group}
        if src in flat:
            return True
    return False


def extract_catalog_equivalence_groups() -> list[set[str]]:
    """Build either/or pairs from 'Not for credit in addition to' catalog notes."""
    groups: list[set[str]] = []
    seen: set[frozenset[str]] = set()
    pattern = re.compile(
        r"not for credit in addition to\s+([A-Z]{3})\s*(\d{3})",
        re.IGNORECASE,
    )
    for code, info in COURSES.items():
        blob = " ".join(
            [
                str(info.get("description") or ""),
                str(info.get("full_title") or ""),
                str(info.get("prerequisites") or ""),
            ]
        )
        for match in pattern.finditer(blob):
            other = _normalize_code(f"{match.group(1)} {match.group(2)}")
            if other not in COURSES or other == code:
                continue
            group = frozenset({code, other})
            if group in seen:
                continue
            seen.add(group)
            groups.append(set(group))
    return groups


_CATALOG_EQUIVALENCE_GROUPS: list[set[str]] | None = None


def catalog_equivalence_groups() -> list[set[str]]:
    global _CATALOG_EQUIVALENCE_GROUPS
    if _CATALOG_EQUIVALENCE_GROUPS is None:
        _CATALOG_EQUIVALENCE_GROUPS = extract_catalog_equivalence_groups()
    return _CATALOG_EQUIVALENCE_GROUPS


def extract_requirement_alternative_groups(requirements_text: str) -> list[set[str]]:
    """All either/or menus for a program: numbered one-of lists + OR lines + catalog pairs."""
    groups = extract_one_of_requirement_groups(requirements_text)
    groups.extend(extract_or_alternative_groups(requirements_text))
    # Catalog equivalences that touch this program's mentioned courses.
    mentioned = {
        _normalize_code(f"{a} {b}")
        for a, b in COURSE_MENTION_RE.findall(requirements_text or "")
    }
    for group in catalog_equivalence_groups():
        if group & mentioned:
            groups.append(set(group))
    # Deduplicate identical sets.
    unique: list[set[str]] = []
    seen: set[frozenset[str]] = set()
    for group in groups:
        key = frozenset(group)
        if len(key) < 2 or key in seen:
            continue
        # Calculus alternatives are owned by CALC_TRACK logic.
        if any(_calc_track_index(code) is not None for code in key):
            continue
        if _is_calc_sequence_group(set(key)):
            continue
        seen.add(key)
        unique.append(set(key))
    return unique


def is_student_choice_equivalent_group(group: set[str]) -> bool:
    """True for clean either/or menus students should pick from (not noisy track fragments)."""
    if len(group) < 2 or len(group) > 4:
        return False
    if any(_calc_track_index(code) is not None for code in group):
        return False
    if _is_calc_sequence_group(group):
        return False
    depts = {code.split()[0] for code in group if " " in code}
    # Physics sequences are multi-course tracks; pairwise OR fragments spam the UI.
    if depts == {"PHY"}:
        return False
    if len(depts) == 1:
        try:
            nums = [int(code.split()[1]) for code in group]
        except (IndexError, ValueError):
            return False
        return max(nums) - min(nums) <= 100
    # Cross-department genetics-style alternatives (BIO 320 / BIO 321 / EBH 302).
    if depts <= {"BIO", "EBH"}:
        return True
    return False


def iter_equivalent_choice_groups(requirements_text: str) -> list[set[str]]:
    """Either/or equivalent course menus suitable for student pickers."""
    groups: list[set[str]] = []
    seen: set[frozenset[str]] = set()
    for group in extract_or_alternative_groups(requirements_text):
        key = frozenset(group)
        if key in seen or not is_student_choice_equivalent_group(group):
            continue
        seen.add(key)
        groups.append(set(group))
    mentioned = {
        _normalize_code(f"{a} {b}")
        for a, b in COURSE_MENTION_RE.findall(requirements_text or "")
    }
    for group in catalog_equivalence_groups():
        if not (group & mentioned):
            continue
        if not is_student_choice_equivalent_group(group):
            continue
        key = frozenset(group)
        if key in seen:
            continue
        seen.add(key)
        groups.append(set(group))
    return groups


def extract_one_of_requirement_groups(requirements_text: str) -> list[set[str]]:
    """Find bulletin 'take one of these' menus (e.g. AMS computing: 325/CSE 101/114/ESG 111).

    Calculus sequences are intentionally NOT injected as flat one-of groups —
    completing AMS 151 must not suppress AMS 161.
    """
    text = requirements_text or ""
    groups: list[set[str]] = []

    parts = re.split(r"(?=\n\s*\d+\.\s+)", text)
    for part in parts:
        head = part[:280]
        # Prefer true "one of" menus; skip "select two/three/four from".
        if re.search(r"(?i)\b(select|choose|take)\s+(two|three|four|2|3|4)\b", head):
            continue
        if not re.search(
            r"(?i)(\bone\b.{0,100}\b(course|from|following|include|among)\b|"
            r"\b(choose|select)\s+one\b|\bone of the following\b)",
            head,
        ):
            continue
        codes: list[str] = []
        for match_obj in COURSE_MENTION_RE.finditer(part):
            code = _normalize_code(f"{match_obj.group(1)} {match_obj.group(2)}")
            if code in COURSES and code not in codes:
                codes.append(code)
        # Keep compact choice menus; huge dumps are full requirement blocks.
        if 2 <= len(codes) <= 10:
            group = set(codes)
            if _is_calc_sequence_group(group):
                continue
            groups.append(group)
    return groups


def extract_calc_track_menu(requirements_text: str) -> list[tuple[str, ...]] | None:
    """If the bulletin asks for one calculus sequence, return the available tracks."""
    text = requirements_text or ""
    if not re.search(
        r"(?i)calculus\s+course\s+sequences?|one of the following calculus|"
        r"calculus\s+sequence",
        text,
    ):
        # Still detect large "one of the following" blocks that list multiple tracks.
        pass
    mentioned = {
        _normalize_code(f"{a} {b}")
        for a, b in COURSE_MENTION_RE.findall(text)
    }
    tracks = [track for track in CALC_TRACKS if set(track) & mentioned]
    if len(tracks) >= 2:
        return tracks
    if re.search(r"(?i)calculus\s+course\s+sequences?|calculus\s+sequence", text):
        return [track for track in CALC_TRACKS if set(track) & mentioned] or None
    return None


def _extract_codes_from_blob(blob: str) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    for match_obj in COURSE_MENTION_RE.finditer(blob or ""):
        code = _normalize_code(f"{match_obj.group(1)} {match_obj.group(2)}")
        if code in COURSES and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def extract_tiered_choice_blocks(requirements_text: str) -> list[dict]:
    """Parse 'at least one from list A, then one more from A or B' style requirements.

    Example (TSM Natural Sciences):
      At least one of the following: BIO 201, CHE 131, GEO 304, ...
      One additional ... from above or the following list: GEO 101, GEO 102, ...
    """
    text = requirements_text or ""
    blocks: list[dict] = []

    for primary_m in re.finditer(
        r"(?is)(?:at least one|select at least one|choose at least one)\s+"
        r"of the following[^\n]{0,120}:?",
        text,
    ):
        window = text[primary_m.end() : primary_m.end() + 2500]
        additional_m = re.search(
            r"(?is)one additional[^\n]{0,160}following[^\n]{0,60}:?",
            window,
        )
        if not additional_m:
            continue

        primary_blob = window[: additional_m.start()]
        secondary_blob = window[additional_m.end() :]
        # Stop secondary list at the next lettered section / note.
        cut = re.search(r"(?m)^(?:[A-Z]\.\s|Note:)", secondary_blob)
        if cut:
            secondary_blob = secondary_blob[: cut.start()]

        primary = set(_extract_codes_from_blob(primary_blob))
        secondary = set(_extract_codes_from_blob(secondary_blob))
        if len(primary) < 2:
            continue
        blocks.append(
            {
                "primary": primary,
                "secondary": secondary,  # options beyond / including extras
                "primary_needed": 1,
                "additional_needed": 1,
            }
        )
    return blocks


def _tiered_blocked_codes(
    blocks: list[dict],
    have: set[str],
    *,
    enforce_primary_first: bool = True,
) -> set[str]:
    """Courses blocked by unmet primary-list / already-satisfied tiered menus."""
    blocked: set[str] = set()
    for block in blocks:
        primary = set(block.get("primary") or [])
        secondary = set(block.get("secondary") or [])
        secondary_only = secondary - primary
        universe = primary | secondary
        if not primary:
            continue
        need_primary = int(block.get("primary_needed") or 1)
        need_additional = int(block.get("additional_needed") or 1)
        primary_have = len(have & primary)
        total_have = len(have & universe)

        # Must finish the required primary pick(s) before secondary-only options.
        if enforce_primary_first and primary_have < need_primary:
            blocked |= secondary_only

        # Whole menu already satisfied — don't keep recommending more from it.
        if primary_have >= need_primary and total_have >= need_primary + need_additional:
            blocked |= universe - have
    return blocked


def _codes_skipped_by_completed_one_of(
    groups: list[set[str]], completed: set[str]
) -> set[str]:
    """If any option in a one-of menu is done, skip recommending the other options.

    Calculus sequences are excluded — AMS 151 must not hide AMS 161.
    Prep/overview math (MAT 122/123) is also skipped once college calculus is done.
    """
    skip: set[str] = set()
    have = {_normalize_code(c) for c in completed}
    if _has_college_calculus(have):
        skip |= PREP_MATH_COURSES - have
    for group in groups:
        if _is_calc_sequence_group(group):
            continue
        if have & group:
            skip |= group - have
    return skip


def extract_program_specializations(program_name: str) -> list[str]:
    """Return specialization / concentration / track names for a major."""
    match = _fuzzy_find_program(program_name)
    if match is None:
        return []
    text = (match.get("requirements_text") or "").replace("\xa0", " ")
    found: list[str] = []
    seen: set[str] = set()

    patterns = [
        r"(?im)^Specialization in ([A-Z][^\n]{2,70})$",
        r"(?im)^Advanced Course Requirements for the Specialization in ([^\n]{2,70})$",
        r"(?im)^Additional Requirements for the ([^\n]{2,50}?) Track:?$",
        r"(?im)^(?:Specialization|Concentration):\s*([A-Z][^\n]{2,60})$",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            name = re.sub(r"\s+", " ", m.group(1)).strip(" .:;-")
            key = name.lower()
            if (
                not name
                or key in seen
                or len(name) < 3
                or key.startswith("the ")
                or "students" in key
                or "must complete" in key
                or "advanced course requirements" in key
            ):
                continue
            seen.add(key)
            found.append(name)

    # Business-style numbered specialization lists under "Area of Specialization".
    area = re.search(
        r"(?is)B\.\s*Area of Specialization(.+?)(?:\nC\.\s|\nD\.\s|\nE\.\s|$)",
        text,
    )
    if not area:
        area = re.search(
            r"(?is)Choose one specialization from the following areas:(.+?)(?:\n[C-Z]\.\s|\nNote:|$)",
            text,
        )
    if area:
        for m in re.finditer(
            r"(?m)^\s*\d+\.\s+([A-Z][A-Za-z /&-]{2,40})\s*$", area.group(1)
        ):
            name = re.sub(r"\s+", " ", m.group(1)).strip()
            key = name.lower()
            if key not in seen and len(name) >= 3:
                seen.add(key)
                found.append(name)

    junk = {
        "core courses",
        "electives",
        "required courses",
        "choose one",
        "students",
        "overview",
        "introduction",
    }
    return [s for s in found if s.lower() not in junk and "option" not in s.lower()]


def extract_specialization_courses(
    program_name: str, specialization: str
) -> list[str]:
    """Courses mentioned under a specific specialization section of the bulletin."""
    match = _fuzzy_find_program(program_name)
    if match is None or not specialization:
        return []
    text = (match.get("requirements_text") or "").replace("\xa0", " ")
    spec = specialization.strip()
    # Locate the specialization heading, then read until the next similar heading.
    heading = re.search(
        rf"(?im)^(?:Specialization in |Advanced Course Requirements for the Specialization in |"
        rf"Additional Requirements for the |(?:\d+\.\s+))?{re.escape(spec)}(?:\s+Track)?\b.*$",
        text,
    )
    if not heading:
        heading = re.search(re.escape(spec), text, flags=re.IGNORECASE)
    if not heading:
        return []
    start = heading.start()
    rest = text[start:]
    nxt = re.search(
        r"(?im)^(?:Specialization in |Advanced Course Requirements for the Specialization in |"
        r"Additional Requirements for the |\d+\.\s+[A-Z])",
        rest[len(heading.group(0)) :],
    )
    chunk = rest if not nxt else rest[: len(heading.group(0)) + nxt.start()]
    ordered: list[str] = []
    seen: set[str] = set()
    for m in COURSE_MENTION_RE.finditer(chunk):
        code = _normalize_code(f"{m.group(1)} {m.group(2)}")
        if code in COURSES and code not in seen:
            seen.add(code)
            ordered.append(code)
    return ordered


def _course_number(code: str) -> int:
    try:
        return int(_normalize_code(code).split()[1])
    except (IndexError, ValueError):
        return 999


def _is_late_stage_major_course(code: str) -> bool:
    """Filter research / senior / internship / upper-standing courses out of early progression."""
    code = _normalize_code(code)
    num = _course_number(code)
    if num >= 400:
        return True
    info = COURSES.get(code) or {}
    title = info.get("full_title") or ""
    prereq = info.get("prerequisites") or ""
    if SPECIAL_COURSE_RE.search(title):
        return True
    # 300-level courses that require U3/U4 standing are not first-semester picks.
    if num >= 300 and STANDING_RE.search(prereq):
        return True
    if num >= 300 and re.search(r"\bU3\b|\bU4\b|\bjunior\b|\bsenior\b", prereq, re.I):
        return True
    return False


def _unlock_counts(required: set[str]) -> dict[str, int]:
    """How many other required courses list this code in a prereq clause."""
    counts = {code: 0 for code in required}
    for other in required:
        for clause in PREREQ_CLAUSES.get(other, []):
            for code in clause:
                if code in counts and code != other:
                    counts[code] += 1
                    break
    return counts


def rank_major_progress_courses(
    majors: list[str],
    completed_courses: list[str] | set[str],
    *,
    is_honors: bool = False,
    limit: int = 20,
    specializations: list[str] | dict[str, str] | None = None,
) -> list[dict]:
    """Rank the next major-required courses a student should prioritize.

    Priority favors:
    1. Eligible courses (prereqs already met)
    2. Lower-level courses (100s before 200s before 300s)
    3. Courses that unlock other major requirements
    4. Courses listed earlier in the bulletin requirements
    5. Specialization courses when a track is selected
    6. Primary major slightly before the second major (callers may interleave)

    Skips alternatives in satisfied "one of" menus (e.g. AMS 325 after CSE 114).
    """
    completed = {_normalize_code(c) for c in completed_courses}
    completed_depts = {c.split()[0] for c in completed if " " in c}

    # Normalize specializations to a list aligned with majors.
    spec_list: list[str] = []
    if isinstance(specializations, dict):
        spec_list = [str(specializations.get(m) or "").strip() for m in (majors or [])]
    elif isinstance(specializations, list):
        spec_list = [str(s or "").strip() for s in specializations]
    while len(spec_list) < len(majors or []):
        spec_list.append("")

    ranked: list[dict] = []
    seen_codes: set[str] = set()

    for major_index, major_name in enumerate(majors or []):
        extracted = extract_program_course_mentions(major_name)
        if not extracted:
            continue
        required = list(extracted["courses"])
        positions = dict(extracted["positions"])
        one_of_skip = _codes_skipped_by_completed_one_of(
            extract_requirement_alternative_groups(extracted.get("requirements_text") or ""),
            completed,
        )
        elective_menu = extract_elective_menu_codes(
            extracted.get("requirements_text") or ""
        )
        tiered_blocks = extract_tiered_choice_blocks(
            extracted.get("requirements_text") or ""
        )
        tiered_skip = _tiered_blocked_codes(
            tiered_blocks, completed, enforce_primary_first=False
        )
        # Prefer primary-list courses when a tiered menu's first pick isn't done yet.
        primary_preferred: set[str] = set()
        for block in tiered_blocks:
            primary = set(block.get("primary") or [])
            if len(completed & primary) < int(block.get("primary_needed") or 1):
                primary_preferred |= primary

        spec_name = spec_list[major_index] if major_index < len(spec_list) else ""
        spec_courses = (
            extract_specialization_courses(major_name, spec_name) if spec_name else []
        )
        spec_set = set(spec_courses)
        # Surface specialization courses even if they appear late on the page.
        for code in spec_courses:
            if code not in positions:
                positions[code] = len(required)
                required.append(code)

        required_set = set(required)
        unlock = _unlock_counts(required_set)
        # Primary departments = frequent requirement depts (not one-off electives).
        dept_freq: dict[str, int] = {}
        for code in required:
            dept = code.split()[0]
            dept_freq[dept] = dept_freq.get(dept, 0) + 1
        primary_depts = {
            dept
            for dept, count in dept_freq.items()
            if count >= 2
        }
        # Always keep the densest early-bulletin departments even if sparse overall.
        early = required[:25]
        early_freq: dict[str, int] = {}
        for code in early:
            dept = code.split()[0]
            early_freq[dept] = early_freq.get(dept, 0) + 1
        for dept, _ in sorted(early_freq.items(), key=lambda kv: (-kv[1], kv[0]))[:3]:
            primary_depts.add(dept)
        # Departments named in the first requirement block are always core
        # (e.g. AMS 151/161 for TSM even when AMS is rare later in the page).
        for code in required[:8]:
            primary_depts.add(code.split()[0])
        if not primary_depts:
            primary_depts = set(early_freq.keys())

        preferred_calc = _preferred_calc_track(required, completed)

        for code in required:
            if code in completed or code in seen_codes:
                continue
            if code in one_of_skip or code in tiered_skip:
                continue
            if code in elective_menu:
                continue
            track_idx = _calc_track_index(code)
            if (
                track_idx is not None
                and preferred_calc is not None
                and track_idx != preferred_calc
            ):
                continue
            if _is_late_stage_major_course(code):
                continue
            info = COURSES.get(code) or {}
            title_blob = (
                f"{info.get('full_title') or ''} "
                f"{info.get('description') or ''} "
                f"{info.get('prerequisites') or ''}"
            )
            if not is_honors and re.search(r"\bhonou?rs?\b", title_blob, re.IGNORECASE):
                continue
            if _is_superseded_by_progress(code, completed):
                continue
            if not _progress_prereqs_satisfied(
                code, completed, majors=[major_name]
            ):
                continue

            dept = code.split()[0]
            level = _course_number(code) // 100
            pos = positions.get(code, 999)
            # Core major courses (primary dept / early in bulletin) outrank
            # optional science/humanities menu courses listed later.
            in_core_dept = dept in primary_depts
            first_requirements = pos < 8  # AMS 151/161 style openers
            early_core = pos < 30
            track_follow = dept in completed_depts
            in_spec = code in spec_set
            in_tier_primary = code in primary_preferred
            ranked.append(
                {
                    "course_code": code,
                    "full_title": (info.get("full_title") or code).replace("\xa0", " "),
                    "credits": _parse_credit_value(info.get("credits", "")),
                    "major": extracted["program_name"] or major_name,
                    "major_index": major_index,
                    "bulletin_position": pos,
                    "unlocks": unlock.get(code, 0),
                    "level": level,
                    "specialization": spec_name or None,
                    "score": (
                        0 if first_requirements else 1,  # bulletin openers win
                        0 if in_tier_primary or not primary_preferred else 1,
                        0 if in_spec or not spec_set else 1,
                        0 if in_core_dept else 1,
                        0 if early_core else 1,
                        0 if level <= 2 else 1,  # finish lower-division cores first
                        0 if track_follow else 1,
                        pos,
                        -unlock.get(code, 0),
                        level,
                        major_index,
                        code,
                    ),
                }
            )
            seen_codes.add(code)

    ranked.sort(key=lambda row: row["score"])
    return ranked[:limit]


def _parse_credit_value(credits_field: str, default: int = 3) -> int:
    """Extract an integer credit value from bulletin credit strings."""
    if not credits_field:
        return default
    match = CREDIT_RE.search(str(credits_field))
    if not match:
        return default
    try:
        return int(float(match.group(1)))
    except ValueError:
        return default


def _build_sbc_index(courses: dict[str, dict]) -> dict[str, list[str]]:
    """Build inverted index: SBC tag -> sorted list of course codes."""
    index: dict[str, list[str]] = {}
    for code, info in courses.items():
        for raw_tag in info.get("sbcs") or []:
            tag = str(raw_tag).strip().upper()
            if not tag:
                continue
            index.setdefault(tag, []).append(code)
    return {tag: sorted(set(codes)) for tag, codes in index.items()}


SBC_INDEX: dict[str, list[str]] = _build_sbc_index(COURSES)


def find_sbc_recommendations(
    completed_courses: list,
    target_sbc: str,
    max_credits: int,
) -> list:
    """Recommend eligible courses that satisfy a target SBC within a credit budget.

    Loads catalog data via the cached COURSES dict, looks up the inverted SBC
    index, then filters to courses the student can take that fit max_credits.
    """
    completed = {_normalize_code(c) for c in completed_courses}
    tag = (target_sbc or "").strip().upper()
    if not tag or max_credits <= 0:
        return []

    recommendations: list[dict] = []
    for code in SBC_INDEX.get(tag, []):
        if code in completed:
            continue
        info = COURSES.get(code) or {}
        credits = _parse_credit_value(info.get("credits", ""))
        if credits <= 0 or credits > max_credits:
            continue
        clauses = PREREQ_CLAUSES.get(code, [])
        if not _prereqs_satisfied(clauses, completed):
            continue
        recommendations.append(
            {
                "course_code": code,
                "full_title": (info.get("full_title") or code).replace("\xa0", " "),
                "credits": credits,
                "sbcs": list(info.get("sbcs") or []),
                "prerequisites": info.get("prerequisites", ""),
                "description": (info.get("description") or "").replace("\xa0", " ").strip(),
            }
        )

    # Prefer an exact credit fit, then smaller courses, then course code.
    recommendations.sort(
        key=lambda row: (
            0 if row["credits"] == max_credits else 1,
            row["credits"],
            row["course_code"],
        )
    )
    return recommendations


def _load_programs(programs_path: Path = PROGRAMS_PATH) -> list[dict]:
    """Load sbu_programs.json as a list of program dictionaries."""
    with open(programs_path, encoding="utf-8") as f:
        programs = json.load(f)
    if not isinstance(programs, list):
        raise ValueError(f"Expected a list in {programs_path}")
    return programs


PROGRAMS: list[dict] = _load_programs()


def _normalize_program_query(program_name: str) -> str:
    cleaned = program_name.replace("\xa0", " ").strip().lower()
    cleaned = re.sub(r"[^\w\s/+.-]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _fuzzy_find_program(program_name: str) -> dict | None:
    """Find the best matching program entry for a fuzzy name query."""
    query = _normalize_program_query(program_name)
    if not query:
        return None

    wants_minor = "minor" in query
    wants_major = any(
        token in query for token in ("major", "bs", "ba", "be", "bfa", "b.s", "b.a")
    )
    # Exact "... Minor" titles should never resolve to a major of the same field.
    if query.endswith(" minor"):
        wants_minor = True
        wants_major = False
    query_core = re.sub(r"\b(minor|major|bs|ba|be|bfa)\b", " ", query)
    query_core = re.sub(r"\s+", " ", query_core).strip()

    best: dict | None = None
    best_score = 0.0

    for program in PROGRAMS:
        name = str(program.get("program_name") or "")
        program_type = str(program.get("type") or "")
        name_norm = _normalize_program_query(name)
        if not name_norm:
            continue

        score = SequenceMatcher(None, query, name_norm).ratio()

        # Boost exact / substring matches.
        if query == name_norm:
            score = 1.0
        elif query in name_norm or name_norm in query:
            score = max(score, 0.92)
        elif query_core and (query_core in name_norm or name_norm.startswith(query_core)):
            score = max(score, 0.88)

        # Prefer the requested major/minor flavor when specified.
        if wants_minor and program_type.lower() == "minor":
            score += 0.08
        elif wants_major and program_type.lower() == "major":
            score += 0.08
        elif wants_minor and program_type.lower() == "major":
            score -= 0.05
        elif wants_major and program_type.lower() == "minor":
            score -= 0.05

        if score > best_score:
            best_score = score
            best = program

    # Require a reasonably confident fuzzy match.
    if best is None or best_score < 0.55:
        return None
    return best


def _normalize_completed(completed_courses: list[str]) -> set[str]:
    return {_normalize_code(c) for c in completed_courses}


@tool
def check_eligibility(target_course: str, completed_courses: list[str]) -> dict:
    """Check whether a student can take a target course given completed courses.

    Returns a dict with eligibility status and any missing prerequisite options.
    OR-groups are satisfied if any listed course is completed.
    """
    target = _normalize_code(target_course)
    if target not in COURSES and target not in PREREQ_GRAPH:
        return {
            "eligible": False,
            "missing_prerequisites": [],
            "error": f"Course not found: {target}",
        }

    completed = _normalize_completed(completed_courses)
    clauses = PREREQ_CLAUSES.get(target, [])
    missing = _missing_prereq_groups(clauses, completed)

    return {
        "eligible": len(missing) == 0,
        "missing_prerequisites": missing,
    }


@tool
def get_next_eligible(
    completed_courses: list[str],
    departments: list[str] | None = None,
) -> list[str]:
    """Return courses the student is strictly eligible for based on prerequisites.

    OR-groups in prerequisite text (e.g. 'AMS 151 or MAT 125 or MAT 131') are
    satisfied when the student completed at least one course in the group.
    Optionally filter by one or more department codes (e.g. ['CSE', 'MAT']) and
    always return at most 40 courses to limit token use.
    """
    completed = _normalize_completed(completed_courses)
    eligible: list[str] = []

    for course, info in COURSES.items():
        if course in completed:
            continue
        clauses = PREREQ_CLAUSES.get(course) or _parse_prereq_clauses(
            info.get("prerequisites", "")
        )
        if _prereqs_satisfied(clauses, completed):
            eligible.append(course)

    if departments:
        eligible = [
            c
            for c in eligible
            if any(c.startswith(d.strip().upper()) for d in departments if d)
        ]

    eligible = sorted(eligible)
    return eligible[:40]


@tool
def get_prereq_chain(course: str) -> list[str]:
    """Return all ancestor prerequisite courses for a given course.

    Ancestors are returned in topological order (foundational courses first).
    """
    target = _normalize_code(course)
    if target not in PREREQ_GRAPH:
        return []

    ancestors = nx.ancestors(PREREQ_GRAPH, target)
    if not ancestors:
        return []

    subgraph = PREREQ_GRAPH.subgraph(ancestors)
    return list(nx.topological_sort(subgraph))


@tool
def get_course_details(course_code: str) -> dict:
    """Return exact scraped catalog details for a course code.

    Normalizes fuzzy codes (e.g. 'CSE214', 'cse 214') to 'CSE 214' before
    looking up the cached sbu_courses.json dictionary, then returns
    full_title, credits, prerequisites, sbcs, and description when found.
    """
    normalized_code = _normalize_code(course_code)
    info = COURSES.get(normalized_code)
    if info is None:
        return {
            "found": False,
            "message": f"Course {normalized_code} not found in scraped catalog.",
        }

    return {
        "found": True,
        "course_code": normalized_code,
        "full_title": info.get("full_title", ""),
        "credits": info.get("credits", ""),
        "prerequisites": info.get("prerequisites", ""),
        "sbcs": info.get("sbcs", []),
        "description": info.get("description", ""),
    }


@tool
def get_program_requirements(program_name: str) -> str:
    """Return degree requirements text for a major or minor.

    Fuzzy-matches program_name against sbu_programs.json (e.g. 'Computer Science'
    or 'Biology minor') and returns the scraped requirements_text describing
    required SBCs, core classes, and credit totals for that degree.
    """
    match = _fuzzy_find_program(program_name)
    if match is None:
        return (
            f"No program matching '{program_name}' was found in the scraped "
            "catalog. Try a name like 'Computer Science' or 'Biology minor'."
        )

    requirements = (match.get("requirements_text") or "").strip()
    if not requirements:
        return (
            f"Matched '{match.get('program_name')}' ({match.get('type')}), but "
            "no requirements text is available in sbu_programs.json."
        )

    # Cap bulletin size so oversized degree pages don't blow the agent context.
    max_chars = 2500
    if len(requirements) > max_chars:
        requirements = (
            requirements[:max_chars].rstrip()
            + "\n\n[Truncated to 2500 characters for token limits.]"
        )

    return (
        f"Program: {match.get('program_name')}\n"
        f"Type: {match.get('type')}\n\n"
        f"{requirements}"
    )


@tool(return_direct=True)
def submit_schedule(schedule_markdown: str) -> str:
    """Deliver the final recommended schedule table to the user.

    Call this tool once you have built the complete Markdown schedule.
    Pass the full finished schedule as schedule_markdown. Because
    return_direct is enabled, LangChain immediately returns this string to
    the user as the final answer.
    """
    return schedule_markdown
