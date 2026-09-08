const HISTORY_KEY = "sbu-counselor-history";

const state = {
  meta: null,
  courses: [],
  completedSbcs: new Set(),
  intake: null,
  menus: [],
  menuSelections: {}, // menuId -> Set(course codes)
  core: null,
  options: [],
  selectedSbcs: new Set(),
  final: null,
  chatHistory: [],
  page: "dashboard", // dashboard | plan | history
  planView: "empty", // last My Plan subview
  history: [],
};

const $ = (sel) => document.querySelector(sel);

function showToast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("is-error", isError);
  el.classList.remove("is-hidden");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => el.classList.add("is-hidden"), 3200);
}

function loadHistory() {
  try {
    const raw = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
    return Array.isArray(raw) ? raw : [];
  } catch {
    return [];
  }
}

function persistHistory() {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(state.history.slice(0, 40)));
}

function showView(name) {
  if (state.page === "plan" && name !== "history") {
    state.planView = name;
  }
  const visible =
    state.page === "history"
      ? "history"
      : state.page === "dashboard"
        ? "empty"
        : name === "history"
          ? state.planView || "empty"
          : name;
  ["empty", "choices", "core", "sbc", "final", "history"].forEach((key) => {
    const el = $(`#view-${key}`);
    if (el) el.classList.toggle("is-hidden", key !== visible);
  });
  updateEmptyCopy();
}

function updateEmptyCopy() {
  const title = $("#empty-title");
  const copy = $("#empty-copy");
  if (!title || !copy) return;
  if (state.page === "dashboard") {
    title.textContent = "Build your semester plan";
    copy.textContent =
      "Configure your major, completed courses, and SBC requirements on the left, then click Generate Optimized Schedule. Results are a planning draft — not final advising.";
  } else if (state.final || state.core || state.menus.length) {
    title.textContent = "Resume your plan";
    copy.textContent =
      "You already have a plan in progress. Use the steps in My Plan, or generate a new one from the Dashboard. Treat every schedule as a draft until advising confirms it.";
  } else {
    title.textContent = "No plan yet";
    copy.textContent =
      "Generate a schedule from the Dashboard, then it will show up here under My Plan. This tool is a planner only — not final registration advice.";
  }
}

function setPage(page) {
  state.page = page;
  $("#workspace")?.setAttribute("data-page", page);
  document.querySelectorAll(".top-nav .nav-link").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.page === page);
  });

  if (page === "history") {
    renderHistory();
    showView("history");
    return;
  }
  if (page === "dashboard") {
    showView("empty");
    return;
  }

  // My Plan — restore the furthest / last valid step
  const available = [];
  if (state.final) available.push("final");
  if (state.options.length) available.push("sbc");
  if (state.core) available.push("core");
  if (state.menus.length) available.push("choices");
  available.push("empty");
  const view = available.includes(state.planView) ? state.planView : available[0];
  showView(view);
}

function goToPlan(view) {
  state.page = "plan";
  $("#workspace")?.setAttribute("data-page", "plan");
  document.querySelectorAll(".top-nav .nav-link").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.page === "plan");
  });
  showView(view);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function normalizeCourse(raw) {
  const m = String(raw || "")
    .toUpperCase()
    .replace(/\s+/g, " ")
    .trim()
    .match(/\b([A-Z]{3})\s*(\d{3})\b/);
  return m ? `${m[1]} ${m[2]}` : null;
}

function clearMajorUntilUserPicks(major) {
  const keepBlank = () => {
    if (major.dataset.userPicked === "1") return;
    major.value = "";
    major.selectedIndex = 0;
  };
  major.dataset.userPicked = "0";
  major.addEventListener(
    "pointerdown",
    () => {
      major.dataset.userPicked = "1";
    },
    { once: true }
  );
  keepBlank();
  // Browsers restore the last chosen major after our script runs.
  setTimeout(keepBlank, 0);
  setTimeout(keepBlank, 300);
  setTimeout(keepBlank, 1000);
}

function renderMajors(majors) {
  const options = majors || [];
  const optionHtml = (includeBlank, blankLabel, selectedValue) =>
    (includeBlank
      ? `<option value="" ${selectedValue ? "" : "selected"}>${blankLabel}</option>`
      : "") +
    options
      .map((name) => {
        const selected = name === selectedValue ? "selected" : "";
        return `<option value="${escapeHtml(name)}" ${selected}>${escapeHtml(name)}</option>`;
      })
      .join("");

  const major = $("#major");
  major.innerHTML = optionHtml(true, "Select your major...", "");
  clearMajorUntilUserPicks(major);
  $("#major-2").innerHTML = optionHtml(true, "Second major (optional)", "");
  syncSecondMajorOptions();
  loadSpecializations("spec-1", "");
  loadSpecializations("spec-2", "");
  updateGenerateEnabled();
}

function renderMinors(minors) {
  const options = minors || [];
  const optionHtml = (blankLabel, selectedValue) =>
    `<option value="">${blankLabel}</option>` +
    options
      .map((name) => {
        const selected = name === selectedValue ? "selected" : "";
        return `<option value="${escapeHtml(name)}" ${selected}>${escapeHtml(name)}</option>`;
      })
      .join("");
  $("#minor").innerHTML = optionHtml("Minor (optional)", "");
  $("#minor-2").innerHTML = optionHtml("Second minor (optional)", "");
  syncMinorOptions();
}

function syncMinorOptions() {
  const primary = $("#minor").value.trim();
  const secondary = $("#minor-2");
  const majors = new Set(selectedMajors().map((m) => m.toLowerCase()));
  [...secondary.options].forEach((opt) => {
    if (!opt.value) return;
    opt.disabled =
      opt.value === primary || majors.has(opt.value.toLowerCase());
    if (opt.disabled && secondary.value === opt.value) secondary.value = "";
  });
  [...$("#minor").options].forEach((opt) => {
    if (!opt.value) return;
    opt.disabled = majors.has(opt.value.toLowerCase());
    if (opt.disabled && $("#minor").value === opt.value) $("#minor").value = "";
  });
}

function selectedMinors() {
  const minors = [];
  const primary = $("#minor").value.trim();
  const secondary = $("#minor-2").value.trim();
  if (primary) minors.push(primary);
  if (secondary && secondary !== primary) minors.push(secondary);
  return minors;
}

async function loadSpecializations(selectId, major) {
  const select = $(`#${selectId}`);
  const blank =
    selectId === "spec-2"
      ? "Second specialization (optional)"
      : "Specialization (optional)";
  if (!major) {
    select.innerHTML = `<option value="">${blank}</option>`;
    select.disabled = true;
    return;
  }
  select.disabled = true;
  select.innerHTML = `<option value="">Loading…</option>`;
  try {
    const data = await fetch(
      `/api/specializations?major=${encodeURIComponent(major)}`
    ).then((r) => r.json());
    const specs = data.specializations || [];
    select.innerHTML =
      `<option value="">${blank}</option>` +
      specs
        .map(
          (name) =>
            `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`
        )
        .join("");
    select.disabled = specs.length === 0;
    if (!specs.length) {
      select.innerHTML = `<option value="">No specializations listed</option>`;
    }
  } catch {
    select.innerHTML = `<option value="">${blank}</option>`;
    select.disabled = true;
  }
}

function syncSecondMajorOptions() {
  const primary = $("#major").value.trim();
  const secondary = $("#major-2");
  [...secondary.options].forEach((opt) => {
    if (!opt.value) return;
    opt.disabled = opt.value === primary;
    if (opt.disabled && secondary.value === opt.value) secondary.value = "";
  });
}

function selectedMajors() {
  const majors = [];
  const primary = $("#major").value.trim();
  const secondary = $("#major-2").value.trim();
  if (primary) majors.push(primary);
  if (secondary && secondary !== primary) majors.push(secondary);
  return majors;
}

function renderCourseTags() {
  const root = $("#course-tags");
  root.innerHTML = state.courses
    .map(
      (code) => `
      <span class="course-tag">
        ${escapeHtml(code)}
        <button type="button" data-code="${escapeHtml(code)}" aria-label="Remove ${escapeHtml(code)}">×</button>
      </span>`
    )
    .join("");
  root.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.courses = state.courses.filter((c) => c !== btn.dataset.code);
      renderCourseTags();
      updateGenerateEnabled();
    });
  });
}

function addCoursesFromDraft(raw) {
  const parts = String(raw || "").split(/[,;\n]+/);
  let added = false;
  for (const part of parts) {
    const code = normalizeCourse(part);
    if (!code) continue;
    if (!state.courses.includes(code)) {
      state.courses.push(code);
      added = true;
    }
  }
  if (added) {
    renderCourseTags();
    updateGenerateEnabled();
  }
  return added;
}

function renderSbcGrid(tags, labels) {
  const grid = $("#sbc-grid");
  grid.innerHTML = tags
    .map((tag) => {
      const title = labels?.[tag] || tag;
      const on = state.completedSbcs.has(tag) ? "is-on" : "";
      return `<button type="button" class="sbc-chip ${on}" data-tag="${tag}" title="${escapeHtml(title)}">${tag}</button>`;
    })
    .join("");

  grid.querySelectorAll(".sbc-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tag = btn.dataset.tag;
      if (state.completedSbcs.has(tag)) state.completedSbcs.delete(tag);
      else state.completedSbcs.add(tag);
      btn.classList.toggle("is-on", state.completedSbcs.has(tag));
      updateSbcHint();
    });
  });
  updateSbcHint();
}

function updateSbcHint() {
  const n = state.completedSbcs.size;
  $("#sbc-hint").textContent = `${n} selected · hover codes for full name.`;
}

function updateCreditBubble() {
  const input = $("#credits");
  const bubble = $("#credit-bubble");
  const min = Number(input.min);
  const max = Number(input.max);
  const val = Number(input.value);
  const pct = (val - min) / (max - min);
  bubble.textContent = `${val} cr`;
  // Keep bubble above thumb; pad edges so it doesn't clip.
  const left = `calc(${pct * 100}% + ${(0.5 - pct) * 14}px)`;
  bubble.style.left = left;
}

function updateGenerateEnabled() {
  const hasMajor = selectedMajors().length > 0;
  const btn = $("#generate-btn");
  const hint = $("#generate-hint");
  btn.disabled = !hasMajor;
  if (!hasMajor) hint.textContent = "Select a major to continue.";
  else if (!state.courses.length)
    hint.textContent = "Add completed courses for better recommendations.";
  else if (selectedMajors().length === 2)
    hint.textContent = "Ready — planning for both majors.";
  else if (selectedMinors().length)
    hint.textContent = "Ready — major + minor requirements will be interleaved.";
  else hint.textContent = "Ready to generate your core plan.";
}

function gatherIntake() {
  const majors = selectedMajors();
  const minors = selectedMinors();
  const credits = Number($("#credits").value) || 15;
  const specs = [];
  if (majors[0]) specs.push($("#spec-1").value.trim());
  if (majors[1]) specs.push($("#spec-2").value.trim());
  return {
    major: majors[0] || "",
    second_major: majors[1] || "",
    majors,
    minor: minors[0] || "",
    second_minor: minors[1] || "",
    minors,
    specializations: specs,
    completed_courses: state.courses.join(", "),
    completed_sbcs: [...state.completedSbcs],
    scheduling_goal: `${credits}-credit full-time semester`,
    target_credits: credits,
    is_honors: $("#is-honors").checked,
    chosen_requirement_courses: selectedRequirementCourses(),
  };
}

function selectedRequirementCourses() {
  const codes = [];
  for (const set of Object.values(state.menuSelections)) {
    for (const code of set) codes.push(code);
  }
  return [...new Set(codes)];
}

function primaryMenusSatisfied() {
  return state.menus
    .filter((m) => m.kind === "tiered_primary")
    .every((m) => (state.menuSelections[m.id] || new Set()).size >= (m.need || 1));
}

function menuIsUnlocked(menu) {
  if (menu.depends_on_kind === "tiered_primary") return primaryMenusSatisfied();
  return true;
}

function syncChoiceCheckboxes() {
  const root = $("#choice-menus");
  if (!root) return;
  for (const menu of state.menus) {
    const selected = state.menuSelections[menu.id] || new Set();
    root.querySelectorAll(`input[data-menu="${CSS.escape(menu.id)}"]`).forEach((input) => {
      const on = selected.has(input.value);
      input.checked = on;
      input.closest(".sbc-option")?.classList.toggle("is-selected", on);
    });
  }
  refreshChoiceMenuLocks();
  updateChoiceMeta();
}

function refreshChoiceBackButtons() {
  const picks = $("#back-to-picks");
  if (picks) picks.classList.toggle("is-hidden", !state.menus.length);
}

function returnToChoicePicks() {
  if (!state.menus.length) {
    setPage("dashboard");
    return;
  }
  syncChoiceCheckboxes();
  goToPlan("choices");
  document.querySelector(".choice-menu")?.scrollIntoView({ block: "start" });
}

function undoLastChoicePick() {
  for (const menu of [...state.menus].reverse()) {
    const selected = state.menuSelections[menu.id];
    if (!selected?.size) continue;
    const last = [...selected].at(-1);
    selected.delete(last);
    const input = document.querySelector(
      `#choice-menus input[data-menu="${CSS.escape(menu.id)}"][value="${CSS.escape(last)}"]`
    );
    if (input) {
      input.checked = false;
      input.closest(".sbc-option")?.classList.remove("is-selected");
    }
    refreshChoiceMenuLocks();
    updateChoiceMeta();
    return true;
  }
  return false;
}

function updateChoiceMeta() {
  const total = selectedRequirementCourses().length;
  const pending = state.menus.filter((m) => {
    if (!menuIsUnlocked(m)) return false;
    const have = (state.menuSelections[m.id] || new Set()).size;
    return have < (m.need || 1);
  }).length;
  const meta = $("#choice-meta");
  if (!state.menus.length) meta.textContent = "No bulletin choices needed";
  else if (pending)
    meta.textContent = `${total} selected · ${pending} menu${pending > 1 ? "s" : ""} still need picks`;
  else meta.textContent = `${total} selected · ready to build core`;
}

function renderChoiceMenus(menus) {
  const root = $("#choice-menus");
  state.menus = menus || [];
  state.menuSelections = {};
  for (const menu of state.menus) state.menuSelections[menu.id] = new Set();

  if (!state.menus.length) {
    root.innerHTML = "<p class='lede'>No open bulletin choice menus for your majors.</p>";
    updateChoiceMeta();
    return;
  }

  root.innerHTML = state.menus
    .map((menu) => {
      const locked = !menuIsUnlocked(menu);
      const cards = (menu.options || [])
        .map((opt) => {
          const desc =
            (opt.description || "No description available.").slice(0, 180) +
            ((opt.description || "").length > 180 ? "…" : "");
          const pills = (opt.sbcs || [])
            .map((s) => `<span class="pill sbc">${escapeHtml(s)}</span>`)
            .join("");
          const tag = opt.list_tag
            ? `<span class="pill">${escapeHtml(opt.list_tag)}</span>`
            : "";
          const note = opt.note
            ? `<p class="menu-note">${escapeHtml(opt.note)}</p>`
            : "";
          return `
            <label class="sbc-option">
              <input type="checkbox" data-menu="${escapeHtml(menu.id)}" value="${escapeHtml(opt.course_code)}" ${locked ? "disabled" : ""} />
              <div>
                <h3>${escapeHtml(opt.full_title)}</h3>
                <div class="meta-row">
                  ${tag}
                  ${pills}
                  <span class="pill">${opt.credits} credits</span>
                  ${opt.ready === false ? '<span class="pill">check prereqs</span>' : ""}
                </div>
                <p>${escapeHtml(desc)}</p>
                ${note}
              </div>
            </label>`;
        })
        .join("");
      return `
        <section class="choice-menu ${locked ? "is-locked" : ""}" data-menu-id="${escapeHtml(menu.id)}">
          <h3>${escapeHtml(menu.title)}</h3>
          <p class="menu-desc">${escapeHtml(menu.description || "")}</p>
          <p class="menu-need">Pick ${menu.need || 1}${locked ? " · unlocks after required-list pick" : ""}</p>
          <div class="sbc-group-list">${cards}</div>
        </section>`;
    })
    .join("");

  root.querySelectorAll("input[type=checkbox]").forEach((input) => {
    input.addEventListener("change", () => {
      const menuId = input.dataset.menu;
      const menu = state.menus.find((m) => m.id === menuId);
      if (!menu) return;
      const selected = state.menuSelections[menuId] || new Set();
      if (input.checked) {
        if (selected.size >= (menu.need || 1)) {
          // Enforce pick limit: replace oldest / uncheck extras.
          const first = [...selected][0];
          selected.delete(first);
          const prev = root.querySelector(
            `input[data-menu="${menuId}"][value="${CSS.escape(first)}"]`
          );
          if (prev) {
            prev.checked = false;
            prev.closest(".sbc-option")?.classList.remove("is-selected");
          }
        }
        selected.add(input.value);
      } else selected.delete(input.value);
      state.menuSelections[menuId] = selected;
      input.closest(".sbc-option")?.classList.toggle("is-selected", input.checked);
      // Re-render lock state for dependent menus without wiping selections.
      refreshChoiceMenuLocks();
      updateChoiceMeta();
    });
  });
  updateChoiceMeta();
}

function refreshChoiceMenuLocks() {
  for (const menu of state.menus) {
    const section = document.querySelector(`[data-menu-id="${menu.id}"]`);
    if (!section) continue;
    const unlocked = menuIsUnlocked(menu);
    section.classList.toggle("is-locked", !unlocked);
    section.querySelectorAll("input[type=checkbox]").forEach((input) => {
      input.disabled = !unlocked;
      if (!unlocked && input.checked) {
        input.checked = false;
        input.closest(".sbc-option")?.classList.remove("is-selected");
        state.menuSelections[menu.id]?.delete(input.value);
      }
    });
    const need = section.querySelector(".menu-need");
    if (need) {
      need.textContent = `Pick ${menu.need || 1}${
        unlocked ? "" : " · unlocks after required-list pick"
      }`;
    }
  }
}

async function buildCoreFromIntake() {
  const payload = {
    ...state.intake,
    chosen_requirement_courses: selectedRequirementCourses(),
  };
  const data = await postJSON("/api/core-schedule", payload);
  state.intake = data.intake;
  state.core = data.schedule;
  state.selectedSbcs = new Set();
  $("#core-summary").textContent =
    `${data.schedule.total_credits} credits locked from major progression` +
    ` · target ${data.intake.target_credits}`;
  renderCourseList($("#core-list"), data.schedule.courses || []);
  refreshChoiceBackButtons();
  goToPlan("core");
}

function renderCourseList(el, courses) {
  el.innerHTML = (courses || [])
    .map((c) => {
      const sbc = (c.sbcs || []).join(", ") || "—";
      return `
        <div class="course-row">
          <strong>${escapeHtml(c.course_code)} · ${escapeHtml(c.title)}</strong>
          <span>${c.credits} credits · ${escapeHtml(c.role)} · SBC: ${escapeHtml(sbc)}</span>
        </div>`;
    })
    .join("");
}

function updatePickMeta() {
  const count = state.selectedSbcs.size;
  const credits = state.options
    .filter((o) => state.selectedSbcs.has(o.course_code))
    .reduce((sum, o) => sum + (o.credits || 0), 0);
  $("#pick-meta").textContent = `${count} selected · ${credits} elective credits`;
}

function renderSbcOptions(options) {
  const root = $("#sbc-options");
  if (!options.length) {
    root.innerHTML =
      "<p class='lede'>No eligible SBC electives found for your remaining gaps.</p>";
    return;
  }

  const labels = state.meta?.sbc_labels || {};
  const groups = new Map();
  for (const opt of options) {
    const key = opt.primary_sbc || (opt.sbcs && opt.sbcs[0]) || "Other";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(opt);
  }

  const optionCard = (opt) => {
    const checked = state.selectedSbcs.has(opt.course_code) ? "checked" : "";
    const selected = checked ? "is-selected" : "";
    const desc =
      (opt.description || "No description available.").slice(0, 220) +
      ((opt.description || "").length > 220 ? "…" : "");
    const pills = (opt.sbcs || [opt.primary_sbc])
      .map((s) => `<span class="pill sbc">${escapeHtml(s)}</span>`)
      .join("");
    return `
      <label class="sbc-option ${selected}">
        <input type="checkbox" value="${escapeHtml(opt.course_code)}" ${checked} />
        <div>
          <h3>${escapeHtml(opt.full_title)}</h3>
          <div class="meta-row">
            ${pills}
            <span class="pill">${opt.credits} credits</span>
          </div>
          <p>${escapeHtml(desc)}</p>
        </div>
      </label>`;
  };

  root.innerHTML = [...groups.entries()]
    .map(([tag, opts]) => {
      const title = labels[tag] ? `${tag} · ${labels[tag]}` : tag;
      return `
        <section class="sbc-group">
          <h3 class="sbc-group-title">${escapeHtml(title)}</h3>
          <div class="sbc-group-list">
            ${opts.map(optionCard).join("")}
          </div>
        </section>`;
    })
    .join("");

  root.querySelectorAll("input[type=checkbox]").forEach((input) => {
    input.addEventListener("change", () => {
      const code = input.value;
      if (input.checked) state.selectedSbcs.add(code);
      else state.selectedSbcs.delete(code);
      root.querySelectorAll(`input[value="${CSS.escape(code)}"]`).forEach((other) => {
        other.checked = input.checked;
        other.closest(".sbc-option")?.classList.toggle("is-selected", input.checked);
      });
      updatePickMeta();
    });
  });
  const search = $("#sbc-search");
  if (search) {
    search.value = "";
    search.oninput = () => {
      const q = search.value.trim().toLowerCase();
      root.querySelectorAll(".sbc-option").forEach((card) => {
        const text = card.textContent.toLowerCase();
        card.classList.toggle("is-hidden", Boolean(q) && !text.includes(q));
      });
      root.querySelectorAll(".sbc-group").forEach((group) => {
        const visible = [...group.querySelectorAll(".sbc-option")].some(
          (card) => !card.classList.contains("is-hidden")
        );
        group.classList.toggle("is-hidden", !visible);
      });
    };
  }
  updatePickMeta();
}

function simpleMarkdown(md) {
  let html = escapeHtml(md);
  html = html.replace(/^### (.*)$/gm, "<h3>$1</h3>");
  html = html.replace(/^## (.*)$/gm, "<h2>$1</h2>");
  html = html.replace(/^# (.*)$/gm, "<h2>$1</h2>");
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

  html = html.replace(/(^\|.+\|\n\|[-:| ]+\|\n(?:\|.+\|\n?)*)/gm, (block) => {
    const lines = block.trim().split("\n").filter(Boolean);
    if (lines.length < 2) return block;
    const split = (line) =>
      line
        .replace(/^\|/, "")
        .replace(/\|$/, "")
        .split("|")
        .map((c) => c.trim());
    const head = split(lines[0]);
    const body = lines.slice(2).map(split);
    const thead = `<tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr>`;
    const tbody = body
      .map((row) => `<tr>${row.map((c) => `<td>${c}</td>`).join("")}</tr>`)
      .join("");
    return `<table><thead>${thead}</thead><tbody>${tbody}</tbody></table>`;
  });

  html = html.replace(/(^(?:- .+(?:\n|$))+)/gm, (block) => {
    const items = block
      .trim()
      .split("\n")
      .map((line) => `<li>${line.replace(/^- /, "")}</li>`)
      .join("");
    return `<ul>${items}</ul>`;
  });

  html = html.replace(/\n{2,}/g, "</p><p>");
  html = `<p>${html}</p>`.replace(/<p><\/p>/g, "");
  return html;
}

async function postJSON(url, payload) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || data.message || `Request failed (${res.status})`);
  }
  return data;
}

function resetPlanViews() {
  state.intake = null;
  state.menus = [];
  state.menuSelections = {};
  state.core = null;
  state.options = [];
  state.selectedSbcs = new Set();
  state.final = null;
  state.chatHistory = [];
  state.planView = "empty";
  const msgs = $("#chat-messages");
  if (msgs) {
    msgs.innerHTML = `
      <div class="chat-bubble assistant">
        Hi — I can explain your plan, suggest swaps, and answer Stony Brook
        questions like add/drop deadlines, full-time load, and SBCs. I’m a
        planner, not a substitute for advising — confirm dates in SOLAR before you register.
      </div>`;
  }
  setPage("dashboard");
}

function formatHistoryDate(iso) {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function saveCurrentPlanToHistory() {
  if (!state.final?.schedule) {
    showToast("Generate a schedule first.", true);
    return;
  }
  const schedule = state.final.schedule;
  const courses = schedule.courses || [];
  const entry = {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    savedAt: new Date().toISOString(),
    label: schedule.major || state.intake?.major || "Semester plan",
    credits: schedule.total_credits,
    courseCodes: courses.map((c) => c.course_code),
    intake: state.intake,
    schedule,
    markdown: state.final.markdown || "",
  };
  state.history = [entry, ...state.history.filter((h) => h.id !== entry.id)];
  persistHistory();
  showToast("Saved to History");
  const btn = $("#save-history");
  if (btn) {
    btn.textContent = "Saved";
    btn.disabled = true;
    setTimeout(() => {
      btn.textContent = "Save to History";
      btn.disabled = false;
    }, 1600);
  }
}

function renderHistory() {
  const root = $("#history-list");
  const clearBtn = $("#clear-history");
  if (!root) return;
  clearBtn?.classList.toggle("is-hidden", !state.history.length);
  if (!state.history.length) {
    root.innerHTML =
      `<p class="history-empty">No saved schedules yet. Generate a plan, then click Save to History.</p>`;
    return;
  }
  root.innerHTML = state.history
    .map((item) => {
      const codes = (item.courseCodes || [])
        .map((c) => `<li>${escapeHtml(c)}</li>`)
        .join("");
      return `
        <article class="history-card" data-id="${escapeHtml(item.id)}">
          <div class="history-card-top">
            <div>
              <strong>${escapeHtml(item.label)}</strong>
              <div class="meta">
                ${escapeHtml(formatHistoryDate(item.savedAt))}
                · ${item.credits ?? "—"} credits
                · ${(item.courseCodes || []).length} courses
              </div>
            </div>
          </div>
          <ul class="history-courses">${codes}</ul>
          <div class="history-card-actions">
            <button class="btn primary" type="button" data-action="open">Open in My Plan</button>
            <button class="btn ghost" type="button" data-action="delete">Remove</button>
          </div>
        </article>`;
    })
    .join("");

  root.querySelectorAll(".history-card").forEach((card) => {
    card.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-action]");
      if (!btn) return;
      const id = card.dataset.id;
      const item = state.history.find((h) => h.id === id);
      if (!item) return;
      if (btn.dataset.action === "delete") {
        state.history = state.history.filter((h) => h.id !== id);
        persistHistory();
        renderHistory();
        showToast("Removed from History");
        return;
      }
      if (btn.dataset.action === "open") {
        openHistoryItem(item);
      }
    });
  });
}

function openHistoryItem(item) {
  state.intake = item.intake || null;
  state.final = {
    intake: item.intake,
    schedule: item.schedule,
    markdown: item.markdown || "",
  };
  state.core = item.schedule;
  state.menus = [];
  state.menuSelections = {};
  state.options = [];
  state.selectedSbcs = new Set();
  const schedule = item.schedule || {};
  const gaps = (schedule.sbc_gaps || []).slice(0, 6).join(", ") || "none";
  $("#final-summary").textContent =
    `${schedule.total_credits || "—"} credits · ${schedule.major || item.label || ""}` +
    ` · gaps left: ${gaps}`;
  $("#final-markdown").innerHTML = simpleMarkdown(item.markdown || "");
  renderCourseList($("#final-list"), schedule.courses || []);
  goToPlan("final");
  showToast("Opened in My Plan");
}

function activeSchedule() {
  if (state.final?.schedule) return state.final.schedule;
  if (state.core) return state.core;
  return { courses: [], total_credits: 0, target_credits: 15 };
}

function applyChatSchedule(schedule) {
  if (!schedule) return;
  if (state.final) {
    state.final = { ...state.final, schedule };
    const gaps = (schedule.sbc_gaps || []).slice(0, 6).join(", ") || "none";
    $("#final-summary").textContent =
      `${schedule.total_credits} credits · ${schedule.major || state.intake?.major || ""}` +
      ` · gaps left: ${gaps}`;
    renderCourseList($("#final-list"), schedule.courses || []);
    const lines = (schedule.courses || [])
      .map(
        (c) =>
          `- **${c.course_code}** — ${c.title} (${c.credits} cr, ${c.role})`
      )
      .join("\n");
    $("#final-markdown").innerHTML = simpleMarkdown(
      `### Updated draft schedule\n\n${lines}\n\n**Total:** ${schedule.total_credits} credits`
    );
    if (state.final) {
      state.final.markdown =
        `### Updated draft schedule\n\n${lines}\n\n**Total:** ${schedule.total_credits} credits`;
    }
    goToPlan("final");
  } else if (state.core) {
    state.core = schedule;
    $("#core-summary").textContent =
      `${schedule.total_credits} credits (updated via counselor)` +
      ` · target ${state.intake?.target_credits || schedule.target_credits || 15}`;
    renderCourseList($("#core-list"), schedule.courses || []);
    goToPlan("core");
  }
}

function appendChatBubble(role, text) {
  const root = $("#chat-messages");
  const div = document.createElement("div");
  div.className = `chat-bubble ${role}`;
  div.textContent = text;
  root.appendChild(div);
  root.scrollTop = root.scrollHeight;
}

function setChatOpen(open) {
  $("#chat-drawer").classList.toggle("is-hidden", !open);
}

async function sendChatMessage(raw) {
  const message = String(raw || "").trim();
  if (!message) return;
  const input = $("#chat-input");
  const sendBtn = $("#chat-send");
  appendChatBubble("user", message);
  state.chatHistory.push({ role: "user", content: message });
  input.value = "";
  sendBtn.disabled = true;
  try {
    const data = await postJSON("/api/chat", {
      message,
      intake: state.intake || {
        completed_courses: state.courses,
        completed_sbcs: [...state.completedSbcs],
        majors: [$("#major")?.value].filter(Boolean),
        major: $("#major")?.value || "",
        target_credits: Number($("#credits")?.value) || 15,
        is_honors: Boolean($("#is-honors")?.checked),
      },
      schedule: activeSchedule(),
      history: state.chatHistory.slice(0, -1),
    });
    const reply = data.reply || "I couldn’t generate a reply.";
    appendChatBubble("assistant", reply);
    state.chatHistory.push({ role: "assistant", content: reply });
    if (data.changed && data.schedule) {
      applyChatSchedule(data.schedule);
      const notes = (data.mutations_applied || []).join(" ");
      appendChatBubble("muted", notes || "Schedule updated.");
      showToast("Schedule updated from chat");
    } else if ((data.mutations_applied || []).length) {
      appendChatBubble("muted", data.mutations_applied.join(" "));
    }
  } catch (err) {
    appendChatBubble("muted", err.message || String(err));
    showToast(err.message || String(err), true);
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
}

async function init() {
  const meta = await fetch("/api/meta").then((r) => r.json());
  state.meta = meta;
  state.history = loadHistory();

  // SBC chips start unselected; select ones already completed.
  state.completedSbcs = new Set();

  renderMajors(meta.majors || []);
  renderMinors(meta.minors || []);
  renderSbcGrid(meta.sbc_tags || [], meta.sbc_labels || {});
  renderCourseTags();
  updateCreditBubble();
  updateGenerateEnabled();
  setPage("dashboard");

  document.querySelectorAll(".top-nav .nav-link").forEach((btn) => {
    btn.addEventListener("click", () => setPage(btn.dataset.page));
  });

  $("#major").addEventListener("change", () => {
    syncSecondMajorOptions();
    syncMinorOptions();
    loadSpecializations("spec-1", $("#major").value.trim());
    updateGenerateEnabled();
  });
  $("#major-2").addEventListener("change", () => {
    syncMinorOptions();
    loadSpecializations("spec-2", $("#major-2").value.trim());
    updateGenerateEnabled();
  });
  $("#minor").addEventListener("change", syncMinorOptions);
  $("#minor-2").addEventListener("change", syncMinorOptions);
  $("#credits").addEventListener("input", updateCreditBubble);

  const draft = $("#course-draft");
  draft.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      if (addCoursesFromDraft(draft.value)) draft.value = "";
      else if (draft.value.trim()) showToast("Use a course code like CSE 114.", true);
    }
  });
  draft.addEventListener("blur", () => {
    if (!draft.value.trim()) return;
    if (addCoursesFromDraft(draft.value)) draft.value = "";
  });

  $("#intake-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("#generate-btn");
    const intake = gatherIntake();
    if (!intake.majors.length) {
      showToast("Select a major to continue.", true);
      return;
    }
    btn.disabled = true;
    btn.textContent = "Loading…";
    try {
      const data = await postJSON("/api/requirement-choices", intake);
      state.intake = data.intake;
      state.selectedSbcs = new Set();
      if ((data.menus || []).length) {
        renderChoiceMenus(data.menus);
        $("#choices-summary").textContent =
          "These lists come from your bulletin requirements. Pick what you want to take.";
        goToPlan("choices");
      } else {
        state.menus = [];
        state.menuSelections = {};
        await buildCoreFromIntake();
      }
    } catch (err) {
      showToast(err.message || String(err), true);
    } finally {
      updateGenerateEnabled();
      btn.textContent = "Generate Optimized Schedule";
    }
  });

  $("#to-core").addEventListener("click", async () => {
    const pending = state.menus.filter((m) => {
      if (!menuIsUnlocked(m)) return true;
      return (state.menuSelections[m.id] || new Set()).size < (m.need || 1);
    });
    if (pending.length) {
      showToast("Finish the required bulletin picks first.", true);
      return;
    }
    const btn = $("#to-core");
    btn.disabled = true;
    try {
      await buildCoreFromIntake();
    } catch (err) {
      showToast(err.message || String(err), true);
    } finally {
      btn.disabled = false;
    }
  });

  $("#back-from-choices").addEventListener("click", () => {
    if (!undoLastChoicePick()) setPage("dashboard");
  });
  $("#back-to-choices").addEventListener("click", returnToChoicePicks);
  $("#back-to-picks").addEventListener("click", returnToChoicePicks);
  $("#back-from-final").addEventListener("click", () => {
    if (state.options.length) goToPlan("sbc");
    else returnToChoicePicks();
  });
  $("#reset-choices").addEventListener("click", resetPlanViews);

  $("#to-sbc").addEventListener("click", async () => {
    const btn = $("#to-sbc");
    btn.disabled = true;
    try {
      const data = await postJSON("/api/sbc-options", {
        ...state.intake,
        completed_courses: state.intake.completed_courses,
        already_selected: [],
        max_credits: 6,
        per_tag_limit: 0,
      });
      state.options = data.options || [];
      $("#sbc-summary").textContent =
        `Core load is ${data.core_credits} credits. ` +
        `You still need about ${data.remaining_credits} credits. ` +
        `Open gaps: ${(data.sbc_gaps || []).join(", ") || "none"}.`;
      renderSbcOptions(state.options);
      goToPlan("sbc");
    } catch (err) {
      showToast(err.message || String(err), true);
    } finally {
      btn.disabled = false;
    }
  });

  $("#finalize").addEventListener("click", async () => {
    const btn = $("#finalize");
    btn.disabled = true;
    btn.textContent = "Generating…";
    try {
      const data = await postJSON("/api/finalize", {
        ...state.intake,
        completed_courses: state.intake.completed_courses,
        chosen_sbc_courses: [...state.selectedSbcs],
      });
      state.final = data;
      $("#final-summary").textContent =
        `${data.schedule.total_credits} credits · ${data.schedule.major}` +
        ` · gaps left: ${(data.schedule.sbc_gaps || []).slice(0, 6).join(", ") || "none"}`;
      $("#final-markdown").innerHTML = simpleMarkdown(data.markdown || "");
      renderCourseList($("#final-list"), data.schedule.courses || []);
      goToPlan("final");
    } catch (err) {
      showToast(err.message || String(err), true);
    } finally {
      btn.disabled = false;
      btn.textContent = "Generate schedule";
    }
  });

  $("#back-to-core").addEventListener("click", () => goToPlan("core"));
  $("#reset-core").addEventListener("click", resetPlanViews);
  $("#start-over").addEventListener("click", resetPlanViews);
  $("#save-history").addEventListener("click", saveCurrentPlanToHistory);
  $("#clear-history").addEventListener("click", () => {
    if (!state.history.length) return;
    if (!confirm("Clear all saved schedules from History?")) return;
    state.history = [];
    persistHistory();
    renderHistory();
    showToast("History cleared");
  });
  $("#chat-fab").addEventListener("click", () => {
    const drawer = $("#chat-drawer");
    setChatOpen(drawer.classList.contains("is-hidden"));
    if (!drawer.classList.contains("is-hidden")) $("#chat-input").focus();
  });
  $("#chat-close").addEventListener("click", () => setChatOpen(false));
  $("#chat-form").addEventListener("submit", (e) => {
    e.preventDefault();
    sendChatMessage($("#chat-input").value);
  });
}

init().catch((err) => showToast(err.message || String(err), true));
