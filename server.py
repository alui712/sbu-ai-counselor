"""FastAPI web server for the SBU AI academic counselor."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope
from pydantic import BaseModel, Field

load_dotenv()

from agent import (  # noqa: E402
    KNOWN_SBC_TAGS,
    build_requirement_choice_menus,
    build_sbc_options_menu,
    build_schedule_deterministically,
    counselor_chat,
    format_schedule_with_llm,
    _extract_completed_courses,
    _parse_credit_target,
    _parse_sbc_tags,
)
from tools.prereq_engine import extract_program_specializations  # noqa: E402

WEB_DIR = Path(__file__).resolve().parent / "web"
PROGRAMS_PATH = Path(__file__).resolve().parent / "sbu_programs.json"

SBC_LABELS = {
    "WRT": "Write Effectively",
    "QPS": "Master Quantitative Problem Solving",
    "ARTS": "Explore and Understand the Arts",
    "HUM": "Confront Contemporary Controversies",
    "SBS": "Understand, Observe, and Analyze Human Behavior",
    "TECH": "Understand Technology",
    "CER": "Respect Diversity and Foster Inclusiveness",
    "STAS": "Understand Relationships between Science or Technology and the Arts, Humanities, or Social Sciences",
    "ESI": "Evaluate and Synthesize Researched Information",
    "GLO": "Engage Global Issues",
    "SNW": "Study the Natural World",
    "USA": "Understand the Political, Economic, Social, and Cultural History of the United States",
    "DIV": "Respect Diversity and Foster Inclusiveness",
    "SPK": "Speak Effectively before an Audience",
    "WRTD": "Write Effectively within One's Discipline",
}


def _load_programs_by_type(program_type: str) -> list[str]:
    if not PROGRAMS_PATH.exists():
        return []
    try:
        programs = json.loads(PROGRAMS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    names = sorted(
        {
            str(p.get("program_name") or "").strip()
            for p in programs
            if str(p.get("type") or "").lower() == program_type.lower()
            and p.get("program_name")
        }
    )
    return names


def _load_majors() -> list[str]:
    return _load_programs_by_type("major")


def _load_minors() -> list[str]:
    return _load_programs_by_type("minor")

app = FastAPI(title="SBU AI Counselor", version="1.0.0")


class IntakeRequest(BaseModel):
    completed_sbcs: list[str] = Field(default_factory=list)
    completed_courses: list[str] | str = Field(default_factory=list)
    scheduling_goal: str = "15-credit full-time semester"
    target_credits: int | None = None
    major: str = "Computer Science"
    second_major: str = ""
    majors: list[str] = Field(default_factory=list)
    minor: str = ""
    second_minor: str = ""
    minors: list[str] = Field(default_factory=list)
    specializations: list[str] = Field(default_factory=list)
    chosen_requirement_courses: list[str] = Field(default_factory=list)
    is_honors: bool = False


def _normalize_name_list(
    *groups: str | list[str] | None,
    limit: int = 2,
) -> list[str]:
    """Collect up to `limit` distinct program names from mixed intake fields."""
    ordered: list[str] = []
    for group in groups:
        values = group if isinstance(group, list) else [group]
        for raw in values:
            name = str(raw or "").strip()
            if not name or name in ordered:
                continue
            ordered.append(name)
            if len(ordered) >= limit:
                return ordered
    return ordered


def _normalize_majors(
    major: str | None = None,
    second_major: str | None = None,
    majors: list[str] | None = None,
) -> list[str]:
    """Collect up to two distinct majors from any of the intake fields."""
    return _normalize_name_list(majors, major, second_major, limit=2) or [
        "Computer Science"
    ]


class CoreRequest(IntakeRequest):
    pass


class SbcOptionsRequest(IntakeRequest):
    already_selected: list[str] = Field(default_factory=list)
    max_credits: int = 4
    per_tag_limit: int = 6
    include_optional: bool = False


class FinalizeRequest(IntakeRequest):
    chosen_sbc_courses: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    message: str
    intake: dict = Field(default_factory=dict)
    schedule: dict = Field(default_factory=dict)
    history: list[dict] = Field(default_factory=list)


def _normalize_intake(body: IntakeRequest) -> dict:
    if isinstance(body.completed_courses, str):
        completed_courses = _extract_completed_courses(body.completed_courses)
    else:
        completed_courses = _extract_completed_courses(
            ", ".join(body.completed_courses)
        )

    completed_sbcs = [
        t.strip().upper() for t in body.completed_sbcs if t and str(t).strip()
    ]
    # Also accept raw comma strings in the list.
    if len(completed_sbcs) == 1 and ("," in completed_sbcs[0] or " " in completed_sbcs[0]):
        completed_sbcs = _parse_sbc_tags(completed_sbcs[0])

    if "WRT 102" in completed_courses and "WRT" not in completed_sbcs:
        completed_sbcs.append("WRT")

    from tools.prereq_engine import COURSES

    for code in completed_courses:
        info = COURSES.get(code) or {}
        for tag in info.get("sbcs") or []:
            tag_u = str(tag).upper()
            if tag_u in KNOWN_SBC_TAGS and tag_u not in completed_sbcs:
                completed_sbcs.append(tag_u)

    target = body.target_credits
    if target is None:
        target = _parse_credit_target(body.scheduling_goal, default=15)

    majors = _normalize_majors(body.major, body.second_major, body.majors)
    minors = _normalize_name_list(body.minors, body.minor, body.second_minor, limit=2)
    # Don't let a minor duplicate an already-selected major title.
    majors_lower = {m.lower() for m in majors}
    minors = [m for m in minors if m.lower() not in majors_lower]
    major_label = " & ".join(majors)
    specs = [str(s or "").strip() for s in (body.specializations or [])]
    while len(specs) < len(majors):
        specs.append("")
    specs = specs[: len(majors)]

    return {
        "completed_sbcs": completed_sbcs,
        "completed_courses": completed_courses,
        "scheduling_goal": body.scheduling_goal
        or f"{target}-credit full-time semester",
        "target_credits": int(target),
        "major": major_label,
        "majors": majors,
        "second_major": majors[1] if len(majors) > 1 else "",
        "minors": minors,
        "minor": minors[0] if minors else "",
        "second_minor": minors[1] if len(minors) > 1 else "",
        "specializations": specs,
        "chosen_requirement_courses": [
            str(c).strip() for c in (body.chosen_requirement_courses or []) if str(c).strip()
        ],
        "is_honors": bool(body.is_honors),
        "sbc_gaps": [tag for tag in KNOWN_SBC_TAGS if tag not in set(completed_sbcs)],
    }


@app.get("/api/meta")
def meta():
    return {
        "sbc_tags": KNOWN_SBC_TAGS,
        "sbc_labels": SBC_LABELS,
        "majors": _load_majors(),
        "minors": _load_minors(),
        "brand": "SBU AI Academic Counselor",
        "default_major": "",
        "default_target_credits": 15,
    }


@app.get("/api/specializations")
def specializations(major: str = ""):
    major = (major or "").strip()
    if not major:
        return {"major": "", "specializations": []}
    return {
        "major": major,
        "specializations": extract_program_specializations(major),
    }


@app.post("/api/requirement-choices")
def requirement_choices(body: IntakeRequest):
    intake = _normalize_intake(body)
    menus = build_requirement_choice_menus(
        majors=intake["majors"],
        minors=intake.get("minors") or [],
        completed_courses=intake["completed_courses"],
        is_honors=intake["is_honors"],
    )
    return {"intake": intake, "menus": menus}


@app.post("/api/core-schedule")
def core_schedule(body: CoreRequest):
    intake = _normalize_intake(body)
    schedule = build_schedule_deterministically(
        completed_courses=intake["completed_courses"],
        major=intake["major"],
        majors=intake["majors"],
        minors=intake.get("minors"),
        completed_sbcs=intake["completed_sbcs"],
        target_credits=intake["target_credits"],
        chosen_sbc_courses=[],
        auto_fill_sbcs=False,
        is_honors=intake["is_honors"],
        specializations=intake.get("specializations"),
        chosen_requirement_courses=intake.get("chosen_requirement_courses"),
    )
    return {"intake": intake, "schedule": schedule}


@app.post("/api/sbc-options")
def sbc_options(body: SbcOptionsRequest):
    intake = _normalize_intake(body)
    already = body.already_selected + intake["completed_courses"]
    # Include core courses already planned so we don't re-offer them.
    core = build_schedule_deterministically(
        completed_courses=intake["completed_courses"],
        major=intake["major"],
        majors=intake["majors"],
        minors=intake.get("minors"),
        completed_sbcs=intake["completed_sbcs"],
        target_credits=intake["target_credits"],
        chosen_sbc_courses=[],
        auto_fill_sbcs=False,
        is_honors=intake["is_honors"],
        specializations=intake.get("specializations"),
        chosen_requirement_courses=intake.get("chosen_requirement_courses"),
    )
    already += [c["course_code"] for c in core["courses"]]
    options = build_sbc_options_menu(
        completed_courses=already,
        completed_sbcs=intake["completed_sbcs"],
        already_selected=body.already_selected,
        max_credits=body.max_credits,
        per_tag_limit=body.per_tag_limit,
        is_honors=intake["is_honors"],
        include_optional=body.include_optional,
    )
    return {
        "intake": intake,
        "core_credits": core["total_credits"],
        "remaining_credits": max(intake["target_credits"] - core["total_credits"], 0),
        "sbc_gaps": core.get("sbc_gaps") or intake["sbc_gaps"],
        "sbc_complete": bool(core.get("sbc_complete")),
        "degree_note": core.get("degree_note") or "",
        "required_courses": [
            {
                "course_code": c.get("course_code"),
                "title": c.get("title"),
                "credits": c.get("credits"),
            }
            for c in (core.get("courses") or [])
            if str(c.get("role") or "").startswith(("core", "minor"))
        ],
        "options": options,
    }


@app.post("/api/finalize")
def finalize(body: FinalizeRequest):
    intake = _normalize_intake(body)
    schedule = build_schedule_deterministically(
        completed_courses=intake["completed_courses"],
        major=intake["major"],
        majors=intake["majors"],
        minors=intake.get("minors"),
        completed_sbcs=intake["completed_sbcs"],
        target_credits=intake["target_credits"],
        chosen_sbc_courses=body.chosen_sbc_courses,
        auto_fill_sbcs=False,
        is_honors=intake["is_honors"],
        specializations=intake.get("specializations"),
        chosen_requirement_courses=intake.get("chosen_requirement_courses"),
    )
    intake["chosen_sbc_courses"] = body.chosen_sbc_courses
    markdown = format_schedule_with_llm(schedule, intake)
    return {
        "intake": intake,
        "schedule": schedule,
        "markdown": markdown,
    }


@app.post("/api/chat")
def chat(body: ChatRequest):
    intake = body.intake or {}
    # Prefer structured intake fields when the client sends a full IntakeRequest-like dict.
    try:
        if body.intake and (
            body.intake.get("completed_courses") is not None
            or body.intake.get("majors")
            or body.intake.get("major")
        ):
            # Soft-normalize when possible without requiring all fields.
            try:
                intake = _normalize_intake(IntakeRequest(**{
                    k: v for k, v in body.intake.items()
                    if k in IntakeRequest.model_fields
                }))
            except Exception:  # noqa: BLE001
                intake = body.intake
    except Exception:  # noqa: BLE001
        intake = body.intake or {}

    schedule = body.schedule or {"courses": [], "total_credits": 0}
    try:
        result = counselor_chat(
            body.message,
            intake=intake,
            schedule=schedule,
            history=body.history,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Counselor chat failed: {exc}") from exc
    return result


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        return response


@app.get("/")
def index():
    index_path = WEB_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Web UI not found")
    return FileResponse(index_path, headers={"Cache-Control": "no-store"})


if WEB_DIR.exists():
    app.mount("/static", NoCacheStaticFiles(directory=WEB_DIR), name="static")
