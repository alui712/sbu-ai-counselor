"""Official-style Stony Brook facts the counselor may use.

Dates are from the Fall 2026 Undergraduate Academic Calendar (revised 3/2/2026)
and the Undergraduate Bulletin add/drop policy. Always tell students dates can
change and to confirm in SOLAR and with their advising office.
"""

from __future__ import annotations

from datetime import date

KNOWLEDGE = """
Stony Brook University (SBU) — campus and registration knowledge for undergraduates.

Registration systems
- SOLAR is the official system to enroll, drop, swap, waitlist, check enrollment appointments, unofficial transcript, and graduation application.
- Brightspace (and class email) is where coursework is delivered; registration itself is not done in Brightspace.
- The Class Schedule in SOLAR shows meeting times, locations, instructors, seats, and whether a course needs permission or is closed.
- This counselor only edits a planning draft. It cannot enroll, drop, or waitlist the student in SOLAR.

Add / drop / withdraw (Fall and spring pattern)
- Add/drop starts the first day of classes and ends at 4:00 PM on the tenth business day of fall or spring classes (close of business).
- Summer six-week sessions: add/drop ends at 4:00 PM on the fifth business day of classes.
- Winter three-week sessions: add/drop ends at 4:00 PM on the third business day of classes.
- Until that 4:00 PM deadline, most adds, drops, and swaps are done in SOLAR. Closed or permission courses still need department or instructor permission even during add/drop.
- After add/drop, adding a course is not a self-service SOLAR change. Students petition for an exception through the process set by their college’s Committee on Academic Standing and Appeals (CASA). Approved late changes can include a petition fee.
- After add/drop, dropping a course records a W (withdrawal) on the transcript.
- Full-time undergraduates must stay at least 12 credits in fall/spring. Dropping below 12 can change full-time status (financial aid, housing, visas).
- A W does not affect GPA the way an F does, but it stays on the transcript.

Fall 2026 undergraduate calendar (subject to change)
- Mon Aug 24, 2026: first day of classes. $50 late registration fee if not enrolled in at least one class before semester start.
- Fri Aug 28, 2026 4:00 PM: last day to waitlist a class; last day to submit major/minor changes effective for Fall.
- Thu Sep 3, 2026 4:00 PM: waitlist process ends. After that, contact the academic department about remaining seats.
- Fri Sep 4, 2026 4:00 PM: last day to add, drop, or otherwise change enrollment (adds, swaps, credit changes) via SOLAR. Last day to drop or submit leave/term withdrawal without a W. Full-time/part-time status locks after this date.
- Mon Sep 7, 2026: Labor Day, no classes.
- Fri Oct 9, 2026 4:00 PM: drop-down deadline for selected AMS, MAT, MAP, and PHY courses (approved adjustment form to the Registrar).
- Mon–Tue Oct 12–13, 2026: Fall Break, no classes.
- Fri Oct 23, 2026 4:00 PM: last day to withdraw from individual courses via SOLAR (W recorded); last day for Grade/Pass/No Credit (GPNC), which is non-petitionable; last day for Section/Credit Change Form without a later petition.
- Mon Oct 26, 2026: students can start submitting major/minor changes effective Spring.
- Mon Nov 2, 2026: advanced registration for Winter and Spring begins by enrollment appointment.
- Wed–Sun Nov 25–29, 2026: Thanksgiving break, no classes.
- Mon Dec 7, 2026 4:00 PM: last day of classes; last day to process leave of absence or term withdrawal via SOLAR.
- Tue Dec 8, 2026: reading day.
- Finals roughly Dec 9–11 and Dec 14–17, 2026. Official end of term Thu Dec 17, 2026.

Fall 2026 tuition liability if the student drops or withdraws (approximate registrar table; confirm with Student Accounts)
- On or before Sun Aug 30, 2026: 0% tuition liability / 100% tuition and fees refunded.
- Aug 31–Sep 6, 2026: 30% tuition incurred.
- Sep 7–Sep 13, 2026: 50% tuition incurred.
- Sep 14–Sep 20, 2026: 70% tuition incurred.
- On or after Sep 21, 2026: 100% tuition liability.

Course load
- Typical full-time undergraduate load is 12–19 credits. 12 credits is the usual full-time minimum.
- More than 19 credits is billed an extra overload fee (Fall 2026 calendar: $20 automatically if enrolled above 19). Increases toward 23 credits follow the Course Load Policy and may need approval.
- This app’s draft cap is a planning limit, not the official SOLAR credit ceiling.

Grades and options
- Letter grades affect GPA. GPNC (Grade/Pass/No Credit) must be selected by the published deadline and cannot be petitioned after that date; not every course or major allows GPNC for degree requirements.
- Repeats: a second attempt can be enrolled in SOLAR under the retake policy; a third or later attempt needs department approval (Third Retake Request Form) and Registrar enrollment.
- Incomplete, P/NC, and major-specific grade rules vary by department — do not invent a course’s grading scheme if it is not in the catalog blurb.

SBCs and degree planning
- The Stony Brook Curriculum (SBC) is the university general-education framework (WRT, QPS, ARTS, HUM, SBS, TECH, CER/DIV, STAS, ESI, GLO, SNW, USA, and related tags).
- A course can satisfy SBC and a major/minor requirement at the same time when the bulletin allows it (double-dipping rules still differ by program).
- Major and minor requirements come from the Undergraduate Bulletin for the student’s catalog year. This planner uses Fall 2026 bulletin text and a course catalog; it is not a degree audit.
- Changing major or minor has calendar deadlines for when the change takes effect. Declaring is usually done through the major department or college advising, then reflected in SOLAR.

Advising and help
- Each college/school has undergraduate advising (for example CEAS, College of Arts and Sciences, College of Business, School of Communication and Journalism, Health Sciences / Nursing, School of Social Welfare). Students should use the advising office that owns their major.
- Academic advising helps with exceptions, petitions, overload, leave, and “can this count.” The Registrar owns calendars, enrollment processing, and transcripts.
- Student Accounts / Bursar handles tuition, fees, and refunds.
- Financial Aid should be asked before dropping below full-time or withdrawing from the term.
- International students should check Visa and Immigration Services before dropping below full-time.
- Accessibility / Student Accessibility Support Center handles academic accommodations.
- If a fact is not in this knowledge pack or the provided catalog/schedule, say you do not have that official detail and point the student to the Registrar academic calendar, Undergraduate Bulletin, SOLAR, or their advising office. Do not guess deadlines, fees, or office hours.

Sources to mention when answering policy questions
- Registrar undergraduate academic calendar (Fall 2026)
- Undergraduate Bulletin: Add/Drop Period
- SOLAR Class Schedule and enrollment screens
""".strip()


def campus_knowledge(*, today: date | None = None) -> str:
    """Return campus knowledge plus a concrete 'today' so deadline answers are relative."""
    today = today or date.today()
    header = (
        f"Today's date for deadline comparisons: {today.isoformat()} "
        f"({today.strftime('%A')}).\n"
        "When a student asks 'what is the latest day to add classes', use the "
        "Fall 2026 undergraduate calendar if the question is about the current "
        "fall term, and compare that deadline to today's date.\n"
    )
    return header + "\n\n" + KNOWLEDGE
