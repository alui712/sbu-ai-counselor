from dotenv import load_dotenv

load_dotenv()

import json
import re
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from tools.prereq_engine import (
    COURSES,
    PREREQ_CLAUSES,
    _normalize_code,
    _prereqs_satisfied,
    find_sbc_recommendations,
    rank_major_progress_courses,
    extract_program_course_mentions,
    extract_tiered_choice_blocks,
    extract_one_of_requirement_groups,
    extract_calc_track_menu,
    _tiered_blocked_codes,
    _progress_prereqs_satisfied,
    _is_superseded_by_progress,
    _is_calc_sequence_group,
    _hard_barriers_clear,
    _is_late_stage_major_course,
    CALC_TRACKS,
    CALC_TRACK_SETS,
    CALC_I_GATEWAYS,
)

DATA_PATH = Path(__file__).resolve().parent / "sbu_courses.json"

# Preferred CSE/MAT progression targets for a typical CS semester.
CORE_PRIORITIES = ["CSE 214", "CSE 215", "MAT 126"]

# Real SBU gen-ed / SBC fillers (never invent placeholders).
GENED_PRIORITIES = ["WRT 102", "ARH 201", "PHI 108", "ECO 108"]

KNOWN_SBC_TAGS = [
    "WRT",
    "QPS",
    "ARTS",
    "HUM",
    "SBS",
    "TECH",
    "CER",
    "STAS",
    "ESI",
    "GLO",
    "SNW",
    "USA",
    "DIV",
]

CREDIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*credits?", re.IGNORECASE)
COURSE_CODE_RE = re.compile(r"\b([A-Za-z]{3})\s*(\d{3})\b")
CREDIT_TARGET_RE = re.compile(r"(\d{1,2})\s*-?\s*(?:to|-)?\s*(\d{0,2})")
HONORS_RE = re.compile(r"\bhonou?rs?\b", re.IGNORECASE)


def _is_honors_course(course_code: str) -> bool:
    """True if the catalog entry is honors-restricted or honors-branded."""
    code = _normalize_code(course_code)
    info = COURSES.get(code) or {}
    blob = " ".join(
        [
            info.get("full_title") or "",
            info.get("description") or "",
            info.get("prerequisites") or "",
            code,
        ]
    )
    return bool(HONORS_RE.search(blob))


def _parse_credits(credits_field: str, default: int = 3, *, title: str = "") -> int:
    """Extract an integer credit value from bulletin credit strings."""
    if credits_field:
        match = CREDIT_RE.search(credits_field)
        if match:
            try:
                return int(float(match.group(1)))
            except ValueError:
                pass
    title_l = (title or "").lower()
    # Labs / problem-solving workshops are almost always 1 credit when blank.
    if re.search(r"laboratory|\blab\b|problem solving", title_l):
        return 1
    return default


def _course_record(course_code: str, role: str) -> dict | None:
    """Build a schedule row from sbu_courses.json for a normalized course code."""
    code = _normalize_code(course_code)
    info = COURSES.get(code)
    if info is None:
        return None

    title = (info.get("full_title") or code).replace("\xa0", " ")
    credits = _parse_credits(info.get("credits", ""), default=3, title=title)
    sbcs = info.get("sbcs") or []
    return {
        "course_code": code,
        "title": title,
        "credits": credits,
        "sbcs": sbcs,
        "role": role,
        "prerequisites": info.get("prerequisites", ""),
    }


def _is_eligible(
    course_code: str,
    completed: set[str],
    *,
    majors: list[str] | None = None,
) -> bool:
    code = _normalize_code(course_code)
    if code not in COURSES:
        return False
    info = COURSES.get(code) or {}
    if not _hard_barriers_clear(
        info.get("prerequisites") or "", completed, majors=majors
    ):
        return False
    clauses = PREREQ_CLAUSES.get(code, [])
    return _prereqs_satisfied(clauses, completed)


def _parse_sbc_tags(raw: str) -> list[str]:
    """Parse comma/space-separated SBC tags from intake input."""
    tokens = re.split(r"[,;\s/|]+", raw.strip().upper())
    tags: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        tag = token.strip().upper().replace("+", "")
        if not tag or tag in {"NONE", "N/A", "NA", "-"}:
            continue
        if tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def _parse_credit_target(raw: str, default: int = 15) -> int:
    """Parse a credit goal like '15', '14-17', or 'around 15 credits'."""
    text = raw.strip().lower()
    if not text:
        return default
    nums = [int(n) for n in re.findall(r"\d{1,2}", text)]
    if not nums:
        return default
    # Prefer a value in the full-time band when multiple numbers appear.
    for n in nums:
        if 12 <= n <= 18:
            return n
    return nums[0]


def _extract_completed_courses(user_text: str) -> list[str]:
    """Pull course codes mentioned in free-form student input."""
    codes = [
        _normalize_code(f"{dept} {num}")
        for dept, num in COURSE_CODE_RE.findall(user_text)
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for code in codes:
        if code not in seen:
            seen.add(code)
            ordered.append(code)
    return ordered


def collect_student_intake() -> dict:
    """Interactive checklist: SBCs, completed courses, and scheduling goal."""
    print("SBU AI Academic Counselor")
    print("=" * 56)
    print("Student intake checklist")
    print("-" * 56)
    print("Common SBC tags: " + ", ".join(KNOWN_SBC_TAGS[:9]))
    print()

    try:
        sbc_raw = input(
            "Select completed SBC categories "
            "(e.g., WRT, QPS, ARTS, HUM, SBS, TECH, CER, STAS, ESI): "
        ).strip()
        courses_raw = input(
            "List your completed courses (e.g., CSE 114, MAT 125): "
        ).strip()
        goal_raw = input(
            "What specific scheduling goal or credit target are you looking for? "
        ).strip()
        major_raw = input(
            "Major(s) (optional, default Computer Science; "
            "use & or comma for double major): "
        ).strip()
        honors_raw = input(
            "Are you in an honors program? (y/N): "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nGoodbye.")
        raise SystemExit(0) from None

    completed_sbcs = _parse_sbc_tags(sbc_raw)
    completed_courses = _extract_completed_courses(courses_raw)
    target_credits = _parse_credit_target(goal_raw, default=15)
    majors = [
        part.strip()
        for part in re.split(r"\s*(?:&|,|/| and )\s*", major_raw, flags=re.IGNORECASE)
        if part.strip()
    ][:2]
    if not majors:
        majors = ["Computer Science"]
    major = " & ".join(majors)
    is_honors = honors_raw in {"y", "yes"}

    # Infer WRT completion if they listed WRT 102 as a course.
    if "WRT 102" in completed_courses and "WRT" not in completed_sbcs:
        completed_sbcs.append("WRT")
    # Infer QPS if they completed a QPS-tagged course like MAT 125/126.
    for code in completed_courses:
        info = COURSES.get(code) or {}
        for tag in info.get("sbcs") or []:
            tag_u = str(tag).upper()
            if tag_u and tag_u not in completed_sbcs:
                # Only auto-add for well-known tags already on the checklist.
                if tag_u in KNOWN_SBC_TAGS:
                    completed_sbcs.append(tag_u)

    payload = {
        "completed_sbcs": completed_sbcs,
        "completed_courses": completed_courses,
        "scheduling_goal": goal_raw or f"{target_credits}-credit full-time semester",
        "target_credits": target_credits,
        "major": major,
        "majors": majors,
        "second_major": majors[1] if len(majors) > 1 else "",
        "is_honors": is_honors,
        "sbc_gaps": [tag for tag in KNOWN_SBC_TAGS if tag not in set(completed_sbcs)],
    }
    return payload


def _option_payload(code: str, *, list_tag: str = "") -> dict | None:
    info = COURSES.get(code) or {}
    if not info:
        return None
    desc = re.sub(r"\s+", " ", (info.get("description") or "").replace("\xa0", " ")).strip()
    return {
        "course_code": code,
        "full_title": (info.get("full_title") or code).replace("\xa0", " "),
        "credits": _parse_credits(info.get("credits", ""), default=3),
        "sbcs": list(info.get("sbcs") or []),
        "description": desc,
        "list_tag": list_tag,
        "prerequisites": info.get("prerequisites", ""),
    }


def build_requirement_choice_menus(
    majors: list[str],
    completed_courses: list[str],
    *,
    minors: list[str] | None = None,
    is_honors: bool = False,
) -> list[dict]:
    """Build interactive pickers for bulletin choice menus (one-of / tiered lists).

    Example: TSM Natural Sciences shows the required primary list, then the
    additional list, so the student can choose instead of auto-assignment.
    Includes minor programs when provided.
    """
    completed = {_normalize_code(c) for c in completed_courses}
    menus: list[dict] = []
    menu_id = 0
    programs = [m for m in (majors or []) if m] + [m for m in (minors or []) if m]

    def eligible_options(codes: set[str], list_tag: str) -> list[dict]:
        out: list[dict] = []
        for code in sorted(codes):
            if code in completed:
                continue
            if not is_honors and _is_honors_course(code):
                continue
            payload = _option_payload(code, list_tag=list_tag)
            if not payload:
                continue
            # Still show bulletin options that need placement / soft barriers.
            ready = (
                _progress_prereqs_satisfied(code, completed, majors=programs)
                and not _is_superseded_by_progress(code, completed)
            )
            payload["ready"] = ready
            if not ready:
                payload["note"] = "May require placement or a prior course — confirm in SOLAR."
            out.append(payload)
        return out

    for major_name in programs:
        extracted = extract_program_course_mentions(major_name)
        if not extracted:
            continue
        text = extracted.get("requirements_text") or ""
        program = extracted.get("program_name") or major_name

        # Calculus sequences: pick a track (first course), not AMS 151 XOR AMS 161.
        calc_tracks = extract_calc_track_menu(text)
        if calc_tracks:
            unfinished = []
            for track in calc_tracks:
                if completed & set(track):
                    continue
                first = next((c for c in track if c not in completed), None)
                if not first:
                    continue
                payload = _option_payload(first, list_tag="calc track")
                if not payload:
                    continue
                payload["title"] = (
                    f"{' → '.join(track)} (start with {first})"
                )
                payload["ready"] = _progress_prereqs_satisfied(
                    first, completed, majors=programs
                )
                unfinished.append(payload)
            if len(unfinished) >= 2:
                menus.append(
                    {
                        "id": f"menu-{menu_id}",
                        "kind": "calc_track",
                        "major": program,
                        "title": f"{program} — choose a calculus sequence",
                        "description": (
                            "Pick one full calculus track. You will take the courses "
                            "in order across semesters (not just one course)."
                        ),
                        "need": 1,
                        "options": unfinished,
                    }
                )
                menu_id += 1

        for block in extract_tiered_choice_blocks(text):
            primary = set(block.get("primary") or [])
            secondary = set(block.get("secondary") or [])
            need_p = int(block.get("primary_needed") or 1)
            need_a = int(block.get("additional_needed") or 1)
            primary_have = len(completed & primary)
            total_have = len(completed & (primary | secondary))

            if primary_have < need_p:
                opts = eligible_options(primary, "required list")
                if opts:
                    menus.append(
                        {
                            "id": f"menu-{menu_id}",
                            "kind": "tiered_primary",
                            "major": program,
                            "title": f"{program} — choose from the required list",
                            "description": (
                                "Select at least one course from this required list "
                                "before additional options."
                            ),
                            "need": need_p - primary_have,
                            "options": opts,
                        }
                    )
                    menu_id += 1

            # Additional picker: show once primary is already satisfied by completed
            # coursework. If primary still open, the UI unlocks this after a pick.
            if primary_have >= need_p and total_have < need_p + need_a:
                opts = eligible_options(primary | secondary, "additional")
                if opts:
                    menus.append(
                        {
                            "id": f"menu-{menu_id}",
                            "kind": "tiered_additional",
                            "major": program,
                            "title": f"{program} — choose one additional course",
                            "description": (
                                "Pick one more course from the required list or the "
                                "additional list."
                            ),
                            "need": need_p + need_a - total_have,
                            "options": opts,
                        }
                    )
                    menu_id += 1
            elif primary_have < need_p:
                # Still expose additional options as a dependent menu for the UI.
                opts = eligible_options(primary | secondary, "additional")
                if opts:
                    menus.append(
                        {
                            "id": f"menu-{menu_id}",
                            "kind": "tiered_additional",
                            "major": program,
                            "title": f"{program} — choose one additional course",
                            "description": (
                                "After your required-list pick, choose one more from "
                                "either list."
                            ),
                            "need": need_a,
                            "depends_on_kind": "tiered_primary",
                            "options": opts,
                        }
                    )
                    menu_id += 1

        for group in extract_one_of_requirement_groups(text):
            # Skip calc-sequence sets (handled as tracks, not flat one-of pickers).
            if _is_calc_sequence_group(group):
                continue
            if any(group == track for track in CALC_TRACK_SETS):
                continue
            if completed & group:
                continue
            opts = eligible_options(group, "one of")
            if len(opts) < 2:
                continue
            menus.append(
                {
                    "id": f"menu-{menu_id}",
                    "kind": "one_of",
                    "major": program,
                    "title": f"{program} — choose one",
                    "description": "Pick one course from this bulletin choice menu.",
                    "need": 1,
                    "options": opts,
                }
            )
            menu_id += 1

    return menus


def build_schedule_deterministically(
    completed_courses: list[str],
    major: str | None = None,
    majors: list[str] | None = None,
    minors: list[str] | None = None,
    completed_sbcs: list[str] | None = None,
    target_credits: int = 15,
    chosen_sbc_courses: list[str] | None = None,
    auto_fill_sbcs: bool = False,
    is_honors: bool = False,
    specializations: list[str] | None = None,
    chosen_requirement_courses: list[str] | None = None,
) -> dict:
    """Resolve a semester schedule from catalog data without calling an LLM.

    Builds core progression courses first. SBC electives are either:
    - chosen interactively by the student (chosen_sbc_courses), or
    - optionally auto-filled when auto_fill_sbcs=True.

    Non-honors students (default) never receive honors-only courses.
    Supports up to two majors and two minors.
    """
    major_list = [m.strip() for m in (majors or []) if m and str(m).strip()]
    if not major_list and major:
        major_list = [
            part.strip()
            for part in re.split(r"\s*(?:&|/)\s*", major)
            if part.strip()
        ][:2]
    if not major_list:
        major_list = ["Computer Science"]
    minor_list = [m.strip() for m in (minors or []) if m and str(m).strip()][:2]
    # Avoid treating the same program as both major and minor.
    major_lower = {m.lower() for m in major_list}
    minor_list = [m for m in minor_list if m.lower() not in major_lower]
    major_label = " & ".join(major_list)
    if minor_list:
        major_label = f"{major_label} | minor: {' & '.join(minor_list)}"

    # Rank majors first, then minors, so gateway major courses stay ahead while
    # minor requirements still enter the interleaved schedule.
    program_list = major_list + minor_list
    spec_list = [str(s or "").strip() for s in (specializations or [])]
    while len(spec_list) < len(major_list):
        spec_list.append("")
    # Minors typically have no specialization selector in the UI.
    while len(spec_list) < len(program_list):
        spec_list.append("")

    completed = {_normalize_code(c) for c in completed_courses}
    done_sbcs = {tag.strip().upper() for tag in (completed_sbcs or []) if tag}
    base_completed_sbcs = set(done_sbcs)
    target = max(12, min(int(target_credits or 15), 18))
    selected: list[dict] = []
    selected_codes: set[str] = set()
    total_credits = 0

    def allowed(code: str) -> bool:
        return is_honors or not _is_honors_course(code)

    def is_progress_course(row: dict) -> bool:
        role = str(row.get("role") or "")
        return role.startswith("core") or role.startswith("minor")

    def progress_count() -> int:
        return len([c for c in selected if is_progress_course(c)])

    def try_add(
        code: str,
        role: str,
        *,
        soft_placement: bool = False,
    ) -> bool:
        """Add a course if hard barriers / prereqs clear.

        Same-semester courses never count as completed prerequisites.
        soft_placement=True allows Calc I / WRT gateways via placement rules.
        """
        nonlocal total_credits
        code = _normalize_code(code)
        if code in completed or code in selected_codes:
            return False
        if not allowed(code):
            return False
        # Avoid non-major survey / topics courses that pollute early schedules.
        if code in {
            "CSE 101",
            "CSE 102",
            "CSE 110",
            "CSE 160",
            "CSE 190",
            "CSE 191",
            "CSE 192",
        }:
            return False
        if _is_late_stage_major_course(code):
            return False
        # Once a calc-I gateway is chosen, don't also pile on AMS 102-style stats
        # from the same department in the first semester.
        if (
            code.startswith("AMS ")
            and code not in {"AMS 151", "AMS 161"}
            and ((completed | selected_codes) & {"AMS 151", "AMS 161"})
        ):
            try:
                if int(code.split()[1]) < 200:
                    return False
            except (IndexError, ValueError):
                pass
        info = COURSES.get(code) or {}
        if not _hard_barriers_clear(
            info.get("prerequisites") or "", completed, majors=program_list
        ):
            return False
        # Keep a single calculus sequence on the draft.
        track_idx = None
        for i, track in enumerate(CALC_TRACK_SETS):
            if code in track:
                track_idx = i
                break
        if track_idx is not None:
            preferred = None
            for i, track in enumerate(CALC_TRACK_SETS):
                if (completed | selected_codes) & track:
                    preferred = i
                    break
            if preferred is not None and track_idx != preferred:
                return False
        if soft_placement or code in CALC_I_GATEWAYS or code == "WRT 102":
            if code == "WRT 102":
                pass  # placement/SAT/AP alternatives — allow for planning drafts
            elif not _progress_prereqs_satisfied(
                code, completed, majors=program_list
            ):
                return False
        else:
            if not _progress_prereqs_satisfied(
                code, completed, majors=program_list
            ):
                return False
        record = _course_record(code, role)
        if record is None:
            return False
        if total_credits + record["credits"] > 17:
            return False
        selected.append(record)
        selected_codes.add(code)
        total_credits += record["credits"]
        return True

    # Student-picked bulletin menu courses first (natural science / one-of lists).
    for code in chosen_requirement_courses or []:
        try_add(code, "core:choice", soft_placement=True)

    # Courses that belong to still-open choice menus should not be auto-filled
    # unless the student already picked them above.
    choice_menu_codes: set[str] = set()
    for program_name in program_list:
        extracted = extract_program_course_mentions(program_name)
        if not extracted:
            continue
        text = extracted.get("requirements_text") or ""
        for block in extract_tiered_choice_blocks(text):
            choice_menu_codes |= set(block.get("primary") or [])
            choice_menu_codes |= set(block.get("secondary") or [])
        for group in extract_one_of_requirement_groups(text):
            if any(group == set(track) for track in CALC_TRACKS):
                continue
            if _is_calc_sequence_group(group):
                continue
            choice_menu_codes |= set(group)

    # 1) Program roadmap (majors first in list, then minors) — gateway courses first.
    program_count = len(program_list)
    core_target = 3
    if program_count >= 2:
        core_target = 4
    if program_count >= 3:
        core_target = 5
    core_target = max(core_target, progress_count())
    roadmap = rank_major_progress_courses(
        program_list,
        completed | selected_codes,
        is_honors=is_honors,
        limit=40,
        specializations=spec_list,
    )
    tiered_blocks: list[dict] = []
    for program_name in program_list:
        extracted = extract_program_course_mentions(program_name)
        if extracted:
            tiered_blocks.extend(
                extract_tiered_choice_blocks(extracted.get("requirements_text") or "")
            )

    # Interleave major/minor requirements so each program gets attention.
    by_major: dict[int, list[dict]] = {}
    for row in roadmap:
        by_major.setdefault(int(row["major_index"]), []).append(row)

    major_indexes = sorted(by_major.keys()) or [0]
    pointers = {idx: 0 for idx in major_indexes}
    while progress_count() < core_target:
        made_progress = False
        for idx in major_indexes:
            if progress_count() >= core_target:
                break
            queue = by_major.get(idx) or []
            while pointers[idx] < len(queue):
                cand = queue[pointers[idx]]
                pointers[idx] += 1
                code = cand["course_code"]
                if not allowed(code):
                    continue
                # Don't auto-assign courses that belong to interactive choice menus.
                if code in choice_menu_codes and code not in {
                    _normalize_code(c) for c in (chosen_requirement_courses or [])
                }:
                    continue
                # Skip peripheral electives / science-menu fillers early on.
                if (
                    int(cand.get("bulletin_position") or 999) >= 12
                    and int(cand.get("unlocks") or 0) < 2
                    and int(cand.get("level") or 9) <= 2
                ):
                    dept = code.split()[0]
                    major_name = str(cand.get("major") or "")
                    if dept not in major_name.upper() and dept not in {
                        "AMS",
                        "MAT",
                        "CSE",
                        "ISE",
                        "WRT",
                    }:
                        # Still allow if this is clearly the program's home dept acronym.
                        home = {
                            w
                            for w in re.findall(r"\b[A-Z]{3}\b", major_name.upper())
                        }
                        if dept not in home:
                            continue
                # Enforce one calculus sequence once any calc course is on the draft.
                track_idx = None
                for i, track in enumerate(CALC_TRACK_SETS):
                    if code in track:
                        track_idx = i
                        break
                if track_idx is not None:
                    preferred = None
                    for i, track in enumerate(CALC_TRACK_SETS):
                        if (completed | selected_codes) & track:
                            preferred = i
                            break
                    if preferred is not None and track_idx != preferred:
                        continue
                # Enforce tiered menus dynamically (primary list before secondary-only).
                if code in _tiered_blocked_codes(
                    tiered_blocks, completed | selected_codes
                ):
                    continue
                program_name = cand.get("major") or program_list[idx]
                is_minor = idx >= len(major_list)
                role = (
                    f"{'minor' if is_minor else 'core'}:{program_name}"
                    if program_count > 1
                    else ("minor" if is_minor else "core")
                )
                if try_add(code, role):
                    made_progress = True
                    break
            if made_progress and len(major_indexes) > 1:
                continue
        if not made_progress:
            break

    # Fallback for CS-heavy students / unmatched majors: classic CSE/MAT ladder.
    stem_like = any(
        re.search(
            r"computer science|data science|information systems|applied math|"
            r"technological systems|engineering",
            m,
            re.I,
        )
        for m in major_list
    )
    if stem_like and progress_count() < core_target:
        for code in CORE_PRIORITIES:
            if progress_count() >= core_target:
                break
            if allowed(code) and _progress_prereqs_satisfied(
                code, completed, majors=program_list
            ):
                try_add(code, "core")

    if stem_like and progress_count() < core_target:
        for code in [
            "CSE 114",
            "AMS 151",
            "MAT 131",
            "MAT 125",
            "CSE 214",
            "CSE 215",
            "AMS 161",
            "MAT 132",
            "MAT 126",
            "CSE 220",
            "AMS 210",
            "MAT 211",
        ]:
            if progress_count() >= core_target:
                break
            if allowed(code) and _progress_prereqs_satisfied(
                code, completed, majors=program_list
            ):
                try_add(code, "core")

    # 2) Writing requirement if WRT SBC not already completed.
    if "WRT" not in done_sbcs and "WRT 102" not in completed:
        if try_add("WRT 102", "gen_ed", soft_placement=True):
            done_sbcs.add("WRT")

    # 3) Student-chosen SBC electives (opinion-based).
    for code in chosen_sbc_courses or []:
        info = COURSES.get(_normalize_code(code)) or {}
        tags = [str(t).upper() for t in (info.get("sbcs") or [])]
        role = f"sbc:{','.join(tags)}" if tags else "sbc"
        if try_add(code, role, soft_placement=True):
            done_sbcs.update(tags)

    # 4) Optional auto-fill (off by default — SBCs are preference-based).
    if auto_fill_sbcs:
        preferred_by_sbc = {
            "ARTS": "ARH 201",
            "HUM": "PHI 108",
            "SBS": "ECO 108",
        }
        for sbc_tag, code in preferred_by_sbc.items():
            if total_credits >= target:
                break
            if sbc_tag in done_sbcs:
                continue
            if try_add(code, f"sbc:{sbc_tag}", soft_placement=True):
                done_sbcs.add(sbc_tag)

        sbc_fill_order = [
            tag
            for tag in [
                "ARTS",
                "HUM",
                "SBS",
                "TECH",
                "GLO",
                "SNW",
                "USA",
                "DIV",
                "ESI",
                "CER",
                "STAS",
            ]
            if tag not in done_sbcs
        ]
        taken_or_done = completed | selected_codes
        while total_credits < target:
            remaining = target - total_credits
            room = 17 - total_credits
            if room <= 0:
                break
            picked = None
            for sbc_tag in sbc_fill_order:
                recs = find_sbc_recommendations(
                    completed_courses=sorted(taken_or_done),
                    target_sbc=sbc_tag,
                    max_credits=max(remaining, room),
                )
                pool = [r for r in recs if r["credits"] <= remaining] or recs
                for rec in pool:
                    if rec["course_code"] in taken_or_done:
                        continue
                    if try_add(rec["course_code"], f"sbc:{sbc_tag}"):
                        picked = rec
                        taken_or_done = completed | selected_codes
                        done_sbcs.add(sbc_tag)
                        break
                if picked:
                    break
            if not picked:
                break

    sbc_gaps = [tag for tag in KNOWN_SBC_TAGS if tag not in done_sbcs]
    return {
        "major": major_label,
        "majors": major_list,
        "minors": minor_list,
        "completed_courses": sorted(completed),
        "base_completed_sbcs": sorted(base_completed_sbcs),
        "completed_sbcs": sorted(done_sbcs),
        "sbc_gaps": sbc_gaps,
        "courses": selected,
        "total_credits": total_credits,
        "target_credits": target,
        "target_credit_range": f"{max(target - 1, 12)}-{min(target + 2, 18)}",
        "roadmap_candidates": [
            {
                "course_code": row["course_code"],
                "major": row["major"],
                "unlocks": row["unlocks"],
                "level": row["level"],
                "specialization": row.get("specialization"),
            }
            for row in roadmap[:8]
        ],
        "specializations": spec_list[: len(major_list)],
    }


def build_sbc_options_menu(
    completed_courses: list[str],
    completed_sbcs: list[str],
    already_selected: list[str] | None = None,
    max_credits: int = 4,
    per_tag_limit: int = 6,
    is_honors: bool = False,
) -> list[dict]:
    """Build a browsable list of eligible SBC electives for student choice.

    Each option includes course name, SBC tags, credits, and a short description.
    Honors courses are hidden unless is_honors=True.

    Picks are diversified by department so the menu is not dominated by
    alphabetically-early codes (AAS, ARH, …).
    """
    completed = {_normalize_code(c) for c in completed_courses}
    completed |= {_normalize_code(c) for c in (already_selected or [])}
    done_sbcs = {t.strip().upper() for t in completed_sbcs if t}
    gap_tags = [
        tag
        for tag in ["ARTS", "HUM", "SBS", "TECH", "GLO", "SNW", "USA", "DIV", "ESI", "CER"]
        if tag not in done_sbcs
    ]

    def diversify(recs: list[dict], limit: int) -> list[dict]:
        """Prefer one course per department, then fill remaining slots."""
        picked: list[dict] = []
        seen: set[str] = set()
        used_depts: set[str] = set()

        def consider(rec: dict, *, require_new_dept: bool) -> bool:
            code = rec["course_code"]
            if code in seen or code in completed:
                return False
            if not is_honors and _is_honors_course(code):
                return False
            dept = code.split()[0] if " " in code else code[:3]
            if require_new_dept and dept in used_depts:
                return False
            desc = rec.get("description") or ""
            if not desc:
                desc = (COURSES.get(code) or {}).get("description", "")
            picked.append(
                {
                    "course_code": code,
                    "full_title": rec["full_title"],
                    "credits": rec["credits"],
                    "sbcs": rec["sbcs"],
                    "primary_sbc": rec.get("primary_sbc") or "",
                    "description": re.sub(r"\s+", " ", desc).strip(),
                }
            )
            seen.add(code)
            used_depts.add(dept)
            return True

        for rec in recs:
            if len(picked) >= limit:
                break
            consider(rec, require_new_dept=True)
        for rec in recs:
            if len(picked) >= limit:
                break
            consider(rec, require_new_dept=False)
        return picked

    options: list[dict] = []
    global_seen: set[str] = set()
    for tag in gap_tags:
        recs = find_sbc_recommendations(
            completed_courses=sorted(completed),
            target_sbc=tag,
            max_credits=max_credits,
        )
        tagged = [{**rec, "primary_sbc": tag} for rec in recs]
        # Skip courses already offered under an earlier SBC gap.
        tagged = [r for r in tagged if r["course_code"] not in global_seen]
        chosen = diversify(tagged, per_tag_limit)
        for opt in chosen:
            opt["primary_sbc"] = tag
            global_seen.add(opt["course_code"])
            options.append(opt)
    return options


def choose_sbc_electives_interactively(
    core_schedule: dict,
    intake: dict,
) -> list[str]:
    """Show SBC course options with descriptions and let the student pick."""
    target = int(intake.get("target_credits") or 15)
    current = int(core_schedule.get("total_credits") or 0)
    remaining = max(target - current, 0)

    print("\n" + "=" * 56)
    print("SBC electives are preference-based — pick what interests you.")
    print(f"Core/writing load so far: {current} credits")
    print(f"Target: {target} credits  |  Still need about: {remaining} credits")
    print("Open SBC gaps: " + (", ".join(core_schedule.get("sbc_gaps") or []) or "None"))
    print("=" * 56)

    if remaining <= 0:
        print("You're already at/above the credit target. Skipping SBC picks.")
        return []

    options = build_sbc_options_menu(
        completed_courses=intake["completed_courses"]
        + [c["course_code"] for c in core_schedule.get("courses", [])],
        completed_sbcs=intake["completed_sbcs"],
        max_credits=min(4, max(remaining, 3)),
        per_tag_limit=4,
        is_honors=bool(intake.get("is_honors")),
    )
    if not options:
        print("No eligible SBC electives found for your remaining gaps.")
        return []

    print("\nAvailable SBC courses:\n")
    for i, opt in enumerate(options, start=1):
        sbc_str = ", ".join(opt["sbcs"]) if opt["sbcs"] else opt["primary_sbc"]
        desc = opt["description"] or "No description available."
        if len(desc) > 160:
            desc = desc[:157].rstrip() + "..."
        print(f"[{i}] {opt['full_title']}")
        print(f"    SBC: {sbc_str}  |  Credits: {opt['credits']}")
        print(f"    {desc}")
        print()

    print(
        "Enter the numbers of the courses you want "
        "(comma-separated), or press Enter to skip."
    )
    print("Example: 1, 4")
    try:
        raw = input("Your SBC picks: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nSkipping SBC picks.")
        return []

    if not raw:
        return []

    chosen: list[str] = []
    running = current
    for token in re.split(r"[,;\s]+", raw):
        if not token.isdigit():
            continue
        idx = int(token)
        if not (1 <= idx <= len(options)):
            print(f"  Ignoring invalid choice: {token}")
            continue
        opt = options[idx - 1]
        if opt["course_code"] in chosen:
            continue
        if running + opt["credits"] > 17:
            print(
                f"  Skipping {opt['course_code']} — would exceed 17 credits "
                f"({running}+{opt['credits']})."
            )
            continue
        chosen.append(opt["course_code"])
        running += opt["credits"]
        print(
            f"  + {opt['course_code']} ({opt['credits']} cr, "
            f"SBC: {', '.join(opt['sbcs'])}) → {running} credits"
        )
        if running >= target:
            break

    return chosen


def format_schedule_markdown(schedule: dict, intake: dict | None = None) -> str:
    """Deterministic Markdown schedule (no LLM). Used as primary fallback."""
    intake = intake or {}
    courses = schedule.get("courses") or []
    lines = [
        f"### Semester plan — {schedule.get('major') or intake.get('major') or 'Student'}",
        "",
        f"**Total credits:** {schedule.get('total_credits', 0)} "
        f"(target {schedule.get('target_credits', 15)})",
        "",
        "| Course Code | Title | Credits | Role / SBC |",
        "|---|---|---:|---|",
    ]
    for row in courses:
        sbc = ", ".join(row.get("sbcs") or []) or "—"
        role = row.get("role") or "—"
        lines.append(
            f"| {row.get('course_code')} | {row.get('title')} | "
            f"{row.get('credits')} | {role} · {sbc} |"
        )
    gaps = schedule.get("sbc_gaps") or []
    done = schedule.get("completed_sbcs") or []
    lines.extend(
        [
            "",
            "**Completed SBCs:** " + (", ".join(done) if done else "None listed"),
            "**Open SBC gaps:** " + (", ".join(gaps) if gaps else "None"),
            "",
            "_This is a planning draft only — not final advising. "
            "Confirm prerequisites, requirements, and seating with academic advising "
            "and in SOLAR before you register._",
        ]
    )
    return "\n".join(lines)


def format_schedule_with_llm(schedule: dict, intake: dict) -> str:
    """Single-shot Groq call: format the precomputed schedule as Markdown."""
    fallback = format_schedule_markdown(schedule, intake)
    try:
        llm = ChatGroq(
            model="openai/gpt-oss-20b",
            reasoning_format="hidden",
            max_retries=2,
        )

        course_lines = []
        for row in schedule["courses"]:
            sbc = ", ".join(row["sbcs"]) if row["sbcs"] else "—"
            course_lines.append(
                f"- {row['course_code']}: {row['title']} | "
                f"{row['credits']} credits | role={row['role']} | SBC={sbc}"
            )
        courses_block = "\n".join(course_lines) or "- (no eligible courses found)"

        system = (
            "You are an SBU Academic Counselor. Format ONLY the provided "
            "pre-selected courses into a friendly Markdown response. "
            "Do not add, remove, or invent courses. Do not change credit counts. "
            "Include a Markdown table with columns: "
            "Course Code | Title | Credits | Requirement/SBC, "
            "then a short bullet-point rationale. "
            "Mention the major(s) and minor(s) when listed, "
            "why core courses were prioritized (prerequisites / major progression), "
            "completed SBCs, remaining SBC gaps, and total credits. "
            "End with a one-line reminder that this is a planning draft only and "
            "the student must confirm with advising and SOLAR before registering."
        )
        roadmap = schedule.get("roadmap_candidates") or []
        roadmap_lines = (
            "\n".join(
                f"- {row['course_code']} ({row['major']}; unlocks {row['unlocks']} later reqs)"
                for row in roadmap
            )
            or "- (none)"
        )
        human = (
            f"Structured intake payload:\n{intake}\n\n"
            f"Major(s): {schedule['major']}\n"
            f"Top roadmap candidates considered:\n{roadmap_lines}\n"
            f"Completed courses: {', '.join(schedule['completed_courses']) or 'None'}\n"
            f"Completed SBCs: {', '.join(schedule.get('completed_sbcs') or []) or 'None'}\n"
            f"SBC gaps still open: {', '.join(schedule.get('sbc_gaps') or []) or 'None'}\n"
            f"Target credits: {schedule.get('target_credits', 15)}\n"
            f"Total credits (precomputed): {schedule['total_credits']}\n\n"
            f"Pre-selected courses (use exactly these):\n{courses_block}\n\n"
            "Write the final Markdown schedule now."
        )

        response = llm.invoke(
            [SystemMessage(content=system), HumanMessage(content=human)]
        )
        content = response.content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            text = "\n".join(parts).strip()
        else:
            text = str(content).strip()
        return text or fallback
    except Exception:  # noqa: BLE001 — rate limits / outages should not block schedules
        return fallback


SCHEDULE_UPDATE_RE = re.compile(
    r"```(?:schedule_update)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)


def _llm_text(content) -> str:
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts).strip()
    return str(content).strip()


def lookup_course_blurb(course_code: str) -> str:
    """Short catalog blurb for counselor context."""
    code = _normalize_code(course_code)
    info = COURSES.get(code)
    if not info:
        return f"{code}: not found in the scraped bulletin catalog."
    title = (info.get("full_title") or code).replace("\xa0", " ")
    credits = info.get("credits") or "?"
    sbcs = ", ".join(info.get("sbcs") or []) or "—"
    prereq = (info.get("prerequisites") or "None listed").replace("\xa0", " ")
    desc = re.sub(r"\s+", " ", (info.get("description") or "").replace("\xa0", " ")).strip()
    if len(desc) > 420:
        desc = desc[:417].rstrip() + "..."
    return (
        f"{title}\nCredits: {credits}\nSBC: {sbcs}\n"
        f"Prerequisites: {prereq}\nDescription: {desc or 'No description.'}"
    )


def _refresh_schedule_sbc_state(schedule: dict) -> dict:
    """Recompute completed_sbcs / sbc_gaps from intake SBCs + scheduled courses."""
    updated = dict(schedule)
    base = {
        str(t).strip().upper()
        for t in (
            schedule.get("base_completed_sbcs")
            or []
        )
        if t
    }
    have = set(base)
    for row in updated.get("courses") or []:
        for tag in row.get("sbcs") or []:
            have.add(str(tag).strip().upper())
    updated["completed_sbcs"] = sorted(have)
    updated["sbc_gaps"] = [tag for tag in KNOWN_SBC_TAGS if tag not in have]
    return updated


def apply_schedule_mutations(schedule: dict, mutations: dict) -> tuple[dict, list[str]]:
    """Apply add/remove/replace mutations to a schedule; return (schedule, notes)."""
    notes: list[str] = []
    courses = list(schedule.get("courses") or [])
    by_code = {_normalize_code(c["course_code"]): c for c in courses}
    completed = {_normalize_code(c) for c in (schedule.get("completed_courses") or [])}
    majors = []
    if schedule.get("majors"):
        majors = list(schedule.get("majors") or [])
    elif schedule.get("major"):
        majors = [schedule.get("major")]
    target = int(schedule.get("target_credits") or 15)
    max_credits = 17

    def total() -> int:
        return sum(int(c.get("credits") or 0) for c in by_code.values())

    for rem in mutations.get("remove") or []:
        code = _normalize_code(str(rem))
        if code in by_code:
            del by_code[code]
            notes.append(f"Removed {code}.")
        else:
            notes.append(f"Could not remove {code} (not on the schedule).")

    for pair in mutations.get("replace") or []:
        if not isinstance(pair, dict):
            continue
        old = _normalize_code(str(pair.get("remove") or ""))
        new = _normalize_code(str(pair.get("add") or ""))
        if not old or not new:
            continue
        if old not in by_code:
            notes.append(f"Could not replace {old} (not on the schedule).")
            continue
        if new in completed:
            notes.append(f"Skipped {new} (already completed).")
            continue
        if new not in COURSES:
            notes.append(f"Skipped {new} (not in catalog).")
            continue
        info = COURSES.get(new) or {}
        if not _hard_barriers_clear(
            info.get("prerequisites") or "", completed, majors=majors
        ):
            notes.append(f"Skipped {new} (standing/major/SBC prerequisite not met).")
            continue
        if not _progress_prereqs_satisfied(
            new, completed, majors=majors
        ):
            notes.append(f"Skipped {new} (prerequisites not met).")
            continue
        old_row = by_code.pop(old)
        role = str(pair.get("role") or old_row.get("role") or "elective")
        record = _course_record(new, role)
        if record is None:
            by_code[old] = old_row
            notes.append(f"Failed to add {new}.")
            continue
        if total() + record["credits"] > max_credits:
            by_code[old] = old_row
            notes.append(f"Could not swap {old}→{new} (would exceed {max_credits} credits).")
            continue
        by_code[new] = record
        notes.append(f"Replaced {old} with {new}.")

    for add in mutations.get("add") or []:
        code = _normalize_code(str(add))
        if code in by_code:
            notes.append(f"{code} is already on the schedule.")
            continue
        if code in completed:
            notes.append(f"Skipped {code} (already completed).")
            continue
        if code not in COURSES:
            notes.append(f"Skipped {code} (not in catalog).")
            continue
        info = COURSES.get(code) or {}
        if not _hard_barriers_clear(
            info.get("prerequisites") or "", completed, majors=majors
        ):
            notes.append(f"Skipped {code} (standing/major/SBC prerequisite not met).")
            continue
        if not _progress_prereqs_satisfied(code, completed, majors=majors):
            notes.append(f"Skipped {code} (prerequisites not met).")
            continue
        record = _course_record(code, "elective")
        if record is None:
            continue
        if total() + record["credits"] > max_credits:
            notes.append(f"Could not add {code} (credit cap).")
            continue
        notes.append(f"Added {code}.")
        by_code[code] = record

    updated = dict(schedule)
    updated["courses"] = list(by_code.values())
    updated["total_credits"] = total()
    updated["target_credits"] = target
    updated = _refresh_schedule_sbc_state(updated)
    return updated, notes


def counselor_chat(
    message: str,
    *,
    intake: dict | None = None,
    schedule: dict | None = None,
    history: list[dict] | None = None,
) -> dict:
    """Conversational SBU counselor that can explain courses and edit the schedule."""
    intake = intake or {}
    schedule = schedule or {"courses": [], "total_credits": 0}
    history = history or []

    # Enrich context with catalog blurbs for current courses + any codes in the question.
    mentioned = COURSE_CODE_RE.findall(message)
    mentioned_codes = {_normalize_code(f"{a} {b}") for a, b in mentioned}
    current_codes = {
        _normalize_code(c.get("course_code", ""))
        for c in (schedule.get("courses") or [])
    }
    # When the student wants swaps, attach a few eligible SBC alternatives.
    alt_codes: set[str] = set()
    want_alts = bool(
        re.search(
            r"\b(swap|replace|change|don'?t like|instead|recommend|alternative|elective)\b",
            message,
            re.I,
        )
    )
    if want_alts:
        try:
            options = build_sbc_options_menu(
                completed_courses=list(intake.get("completed_courses") or []),
                completed_sbcs=list(intake.get("completed_sbcs") or []),
                already_selected=list(current_codes),
                max_credits=4,
                per_tag_limit=3,
                is_honors=bool(intake.get("is_honors")),
            )
            alt_codes = {
                _normalize_code(o["course_code"])
                for o in options[:18]
                if o.get("course_code")
            }
        except Exception:  # noqa: BLE001
            alt_codes = set()

    blurbs = []
    for code in sorted(current_codes | mentioned_codes | alt_codes):
        if code in COURSES:
            blurbs.append(lookup_course_blurb(code))
    catalog_block = "\n\n".join(blurbs) or "(no catalog blurbs)"

    course_lines = []
    for row in schedule.get("courses") or []:
        sbc = ", ".join(row.get("sbcs") or []) or "—"
        course_lines.append(
            f"- {row.get('course_code')}: {row.get('title')} | "
            f"{row.get('credits')} cr | role={row.get('role')} | SBC={sbc}"
        )
    schedule_block = "\n".join(course_lines) or "- (empty schedule)"

    system = (
        "You are the SBU AI Academic Counselor for Stony Brook University. "
        "Help undergraduates understand their semester plan, prerequisites, SBCs, "
        "majors/minors, and what courses cover. Be concrete and concise.\n\n"
        "Rules:\n"
        "- Use ONLY the provided intake, schedule, and catalog blurbs. "
        "Do not invent course codes, titles, or requirements.\n"
        "- If the student dislikes a course, suggest real alternatives from the "
        "catalog blurbs when possible, or ask which SBC/major need they want to keep.\n"
        "- To change the schedule, append ONE fenced JSON block at the end using "
        "language tag schedule_update with this shape:\n"
        '```schedule_update\n'
        '{"replace":[{"remove":"GEO 101","add":"CHE 131"}],'
        '"add":["PHI 108"],"remove":["AMS 301"]}\n'
        "```\n"
        "- Only include codes that exist in the catalog blurbs or current schedule "
        "unless the student explicitly named a valid SBU code.\n"
        "- If no schedule change is needed, do NOT include a schedule_update block.\n"
        "- Never claim to enroll the student; you only edit this planning draft."
    )
    human = (
        f"Student intake:\n{intake}\n\n"
        f"Current draft schedule ({schedule.get('total_credits', 0)} credits, "
        f"target {schedule.get('target_credits', 15)}):\n{schedule_block}\n\n"
        f"Catalog details:\n{catalog_block}\n\n"
        f"Student message:\n{message}"
    )

    llm = ChatGroq(
        model="openai/gpt-oss-20b",
        reasoning_format="hidden",
        max_retries=2,
        temperature=0.3,
    )
    messages = [SystemMessage(content=system)]
    for turn in history[-8:]:
        role = (turn.get("role") or "").lower()
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        if role == "assistant":
            messages.append(SystemMessage(content=f"Prior counselor reply:\n{content}"))
        else:
            messages.append(HumanMessage(content=content))
    messages.append(HumanMessage(content=human))

    try:
        raw = _llm_text(llm.invoke(messages).content)
    except Exception as exc:  # noqa: BLE001
        # Offline / rate-limit fallback: answer from catalog blurbs only.
        focus = sorted(mentioned_codes | current_codes)[:6]
        blurbs_short = "\n\n".join(
            lookup_course_blurb(c) for c in focus if c in COURSES
        ) or "I don't have catalog text for that yet."
        return {
            "reply": (
                "I'm having trouble reaching the language model right now "
                f"({type(exc).__name__}). Here is what the bulletin catalog says "
                "about the courses on your plan / in your question:\n\n"
                f"{blurbs_short}\n\n"
                "Try again in a minute for schedule edits."
            ),
            "schedule": schedule,
            "mutations_applied": [],
            "changed": False,
        }
    mutations = None
    reply = raw
    match = SCHEDULE_UPDATE_RE.search(raw)
    if match:
        reply = (raw[: match.start()] + raw[match.end() :]).strip()
        try:
            mutations = json.loads(match.group(1))
        except json.JSONDecodeError:
            mutations = None
            reply = raw.strip()

    notes: list[str] = []
    updated = schedule
    if isinstance(mutations, dict) and any(
        mutations.get(k) for k in ("add", "remove", "replace")
    ):
        updated, notes = apply_schedule_mutations(schedule, mutations)

    before_codes = [
        _normalize_code(c.get("course_code", ""))
        for c in (schedule.get("courses") or [])
    ]
    after_codes = [
        _normalize_code(c.get("course_code", ""))
        for c in (updated.get("courses") or [])
    ]
    return {
        "reply": reply,
        "schedule": updated,
        "mutations_applied": notes,
        "changed": before_codes != after_codes,
    }


def main() -> None:
    if not DATA_PATH.exists() and not COURSES:
        raise FileNotFoundError(f"Missing course catalog: {DATA_PATH}")

    while True:
        print()
        intake = collect_student_intake()
        print("\nIntake captured:")
        print(f"  Completed SBCs : {', '.join(intake['completed_sbcs']) or 'None'}")
        print(f"  SBC gaps       : {', '.join(intake['sbc_gaps']) or 'None'}")
        print(f"  Completed courses: {', '.join(intake['completed_courses']) or 'None'}")
        print(f"  Goal           : {intake['scheduling_goal']}")
        print(f"  Target credits : {intake['target_credits']}")
        print(f"  Major(s)       : {intake['major']}")
        print(f"  Honors         : {'yes' if intake.get('is_honors') else 'no'}")
        print("\nBuilding core schedule first...\n")

        try:
            core_schedule = build_schedule_deterministically(
                completed_courses=intake["completed_courses"],
                major=intake["major"],
                majors=intake.get("majors"),
                completed_sbcs=intake["completed_sbcs"],
                target_credits=intake["target_credits"],
                chosen_sbc_courses=[],
                auto_fill_sbcs=False,
                is_honors=bool(intake.get("is_honors")),
            )
            print("Core / required courses so far:")
            for row in core_schedule["courses"]:
                print(f"  - {row['course_code']}: {row['title']} ({row['credits']} cr)")
            print(f"  Subtotal: {core_schedule['total_credits']} credits")

            chosen_sbcs = choose_sbc_electives_interactively(core_schedule, intake)
            schedule = build_schedule_deterministically(
                completed_courses=intake["completed_courses"],
                major=intake["major"],
                majors=intake.get("majors"),
                completed_sbcs=intake["completed_sbcs"],
                target_credits=intake["target_credits"],
                chosen_sbc_courses=chosen_sbcs,
                auto_fill_sbcs=False,
                is_honors=bool(intake.get("is_honors")),
            )
            intake["chosen_sbc_courses"] = chosen_sbcs
            output = format_schedule_with_llm(schedule, intake)
        except Exception as exc:  # noqa: BLE001 — keep CLI resilient
            print(f"Counselor: Sorry, something went wrong: {exc}\n")
        else:
            print(output)
            print()

        try:
            again = input("Build another schedule? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break
        if again not in {"y", "yes"}:
            print("Goodbye.")
            break


if __name__ == "__main__":
    main()
