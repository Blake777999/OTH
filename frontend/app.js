/**
 * Old Town Hours Scheduling System — Frontend Application Logic
 */

// Global App State
const state = {
  currentWeekId: "",
  members: [],
  scheduleData: null,
  activeTab: "tab-schedule",
  
  // Master Painter State
  selectedMasterMemberId: null,
  masterBusySlots: new Set(), // strings like "d,s" (e.g. "0,3")
  
  // Override Painter State
  selectedOverrideMemberId: null,
  overrideBusySlots: new Set(), // strings like "d,s"
  
  // Drag-paint tracking
  isPainting: false,
  paintMode: "busy", // "busy" or "available"
  
  // Filters
  houseFilter: "all",
  memberFilter: "all",
  
  // Google Sheets
  sheetsConfig: null,
};

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const SLOTS_PER_DAY = 20;
const START_HOUR = 9;

// Helpers
function slotToTimeStr(s) {
  const totalMin = START_HOUR * 60 + s * 30;
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  const ampm = h < 12 ? "AM" : "PM";
  const displayH = h <= 12 ? h : h - 12;
  const finalH = displayH === 0 ? 12 : displayH;
  return `${finalH}:${m.toString().padStart(2, "0")} ${ampm}`;
}

function slotRangeStr(s) {
  return `${slotToTimeStr(s)} – ${slotToTimeStr(s + 1)}`;
}

function showToast(message, type = "success") {
  const container = document.getElementById("toast-container");
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<span>${type === "success" ? "✅" : "⚠️"}</span> <span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

// Format ISO Week
function formatWeekDisplay(weekId) {
  return weekId.replace("-W", " • Week ");
}

// ----------------- Initialization -----------------

function safeAddListener(id, event, handler) {
  const el = document.getElementById(id);
  if (el) {
    el.addEventListener(event, handler);
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  try { setupNavigation(); } catch (e) { console.error("setupNavigation error:", e); }
  try { setupEventListeners(); } catch (e) { console.error("setupEventListeners error:", e); }
  
  // Fetch initial weeks and members
  try { await loadWeeks(); } catch (e) { console.error("loadWeeks error:", e); }
  try { await loadMembers(); } catch (e) { console.error("loadMembers error:", e); }
  try { await loadSchedule(); } catch (e) { console.error("loadSchedule error:", e); }

  // Setup painter grids
  try { buildMasterPainterTable(); } catch (e) { console.error("buildMasterPainterTable error:", e); }
  try { buildOverridePainterTable(); } catch (e) { console.error("buildOverridePainterTable error:", e); }
});

function setupNavigation() {
  const tabs = document.querySelectorAll(".tab-btn");
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      const target = tab.dataset.tab;
      document.querySelectorAll(".tab-btn").forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".view-section").forEach(s => s.classList.remove("active"));

      tab.classList.add("active");
      const targetEl = document.getElementById(target);
      if (targetEl) targetEl.classList.add("active");
      state.activeTab = target;

      if (target === "tab-master") {
        syncMasterPainter();
      } else if (target === "tab-overrides") {
        syncOverridePainter();
      } else if (target === "tab-roster") {
        renderRosterCards();
      }
    });
  });
}

function setupEventListeners() {
  // Week navigation
  safeAddListener("btn-prev-week", "click", () => navigateWeek(-1));
  safeAddListener("btn-next-week", "click", () => navigateWeek(1));
  safeAddListener("btn-current-week", "click", () => navigateToCurrentWeek());

  // Filters
  safeAddListener("house-filter", "change", (e) => {
    state.houseFilter = e.target.value;
    renderScheduleGrid();
  });
  safeAddListener("member-filter", "change", (e) => {
    state.memberFilter = e.target.value;
    renderScheduleGrid();
  });

  // Schedule Action Buttons
  safeAddListener("btn-open-generate", "click", openGenerateModal);
  safeAddListener("modal-gen-close", "click", closeGenerateModal);
  safeAddListener("btn-cancel-gen", "click", closeGenerateModal);
  safeAddListener("btn-run-gen", "click", runScheduleGeneration);

  // Copy for Google Sheets
  safeAddListener("btn-copy-sheets", "click", copyForGoogleSheets);
  safeAddListener("btn-copy-sheets-tab", "click", copyForGoogleSheets);

  // Master schedule member select
  safeAddListener("master-member-select", "change", (e) => {
    state.selectedMasterMemberId = e.target.value;
    syncMasterPainter();
  });

  // Master painter actions
  safeAddListener("btn-save-master", "click", saveMasterSchedule);
  safeAddListener("btn-clear-master", "click", clearMasterSchedule);

  // Override painter member select
  safeAddListener("override-member-select", "change", (e) => {
    state.selectedOverrideMemberId = e.target.value;
    syncOverridePainter();
  });
  safeAddListener("btn-save-overrides", "click", saveWeeklyOverrides);
  safeAddListener("btn-clear-overrides", "click", clearWeeklyOverrides);

  // Member Modal
  safeAddListener("btn-add-member", "click", () => openMemberModal(null));
  safeAddListener("modal-member-close", "click", closeMemberModal);
  safeAddListener("btn-cancel-member", "click", closeMemberModal);
  safeAddListener("member-form", "submit", handleMemberFormSubmit);
  safeAddListener("btn-delete-member", "click", handleMemberDelete);

  // Global mouse up for painter grids
  window.addEventListener("mouseup", () => {
    state.isPainting = false;
  });
}

// ----------------- Week Navigation -----------------

async function loadWeeks() {
  try {
    const res = await fetch("/api/schedules-weeks");
    const data = await res.json();
    state.currentWeekId = data.current_week;
    updateWeekHeaderDisplay();
  } catch (err) {
    console.error("Failed to load weeks:", err);
    state.currentWeekId = "2026-W40";
    updateWeekHeaderDisplay();
  }
}

function updateWeekHeaderDisplay() {
  const weekLabel = document.getElementById("current-week-label");
  if (weekLabel) weekLabel.textContent = formatWeekDisplay(state.currentWeekId);
  const overrideBadge = document.getElementById("override-week-badge");
  if (overrideBadge) overrideBadge.textContent = formatWeekDisplay(state.currentWeekId);

  const xlsxUrl = `/api/schedules/${state.currentWeekId}/export.xlsx`;
  const csvUrl = `/api/schedules/${state.currentWeekId}/export.csv`;

  const btnXlsx = document.getElementById("btn-download-xlsx");
  if (btnXlsx) btnXlsx.href = xlsxUrl;
  const btnXlsxTab = document.getElementById("btn-download-xlsx-tab");
  if (btnXlsxTab) btnXlsxTab.href = xlsxUrl;

  const btnCsv = document.getElementById("btn-download-csv");
  if (btnCsv) btnCsv.href = csvUrl;
  const btnCsvTab = document.getElementById("btn-download-csv-tab");
  if (btnCsvTab) btnCsvTab.href = csvUrl;
}

function navigateWeek(delta) {
  const parts = state.currentWeekId.split("-W");
  let year = parseInt(parts[0]);
  let week = parseInt(parts[1]) + delta;
  if (week < 1) {
    year -= 1;
    week = 52;
  } else if (week > 52) {
    year += 1;
    week = 1;
  }
  state.currentWeekId = `${year}-W${week.toString().padStart(2, "0")}`;
  updateWeekHeaderDisplay();
  loadSchedule();
  if (state.activeTab === "tab-overrides") {
    syncOverridePainter();
  }
}

async function navigateToCurrentWeek() {
  await loadWeeks();
  loadSchedule();
  if (state.activeTab === "tab-overrides") {
    syncOverridePainter();
  }
}

// ----------------- Member Data -----------------

async function loadMembers() {
  try {
    const res = await fetch("/api/members");
    state.members = await res.json();

    // Populate dropdowns
    populateMemberDropdowns();
    renderRosterCards();
  } catch (err) {
    console.error("Failed to load members:", err);
  }
}

function populateMemberDropdowns() {
  const masterSelect = document.getElementById("master-member-select");
  const overrideSelect = document.getElementById("override-member-select");
  const filterSelect = document.getElementById("member-filter");

  masterSelect.innerHTML = "";
  overrideSelect.innerHTML = "";
  filterSelect.innerHTML = '<option value="all">Highlight: All Members</option>';

  state.members.forEach(m => {
    const activePrefix = m.active ? "" : "[Inactive] ";
    masterSelect.innerHTML += `<option value="${m.id}">${activePrefix}${m.name}</option>`;
    overrideSelect.innerHTML += `<option value="${m.id}">${activePrefix}${m.name}</option>`;
    if (m.active) {
      filterSelect.innerHTML += `<option value="${m.id}">${m.name}</option>`;
    }
  });

  if (!state.selectedMasterMemberId && state.members.length > 0) {
    state.selectedMasterMemberId = state.members[0].id;
  }
  if (!state.selectedOverrideMemberId && state.members.length > 0) {
    state.selectedOverrideMemberId = state.members[0].id;
  }
}

// ----------------- Schedule Loading & Rendering -----------------

async function loadSchedule() {
  try {
    const res = await fetch(`/api/schedules/${state.currentWeekId}`);
    state.scheduleData = await res.json();
    renderScheduleGrid();
    renderMetrics();
    renderSummaryTable();
  } catch (err) {
    console.error("Failed to load schedule:", err);
  }
}

function renderMetrics() {
  const s = state.scheduleData?.stats;
  const activeMembersCount = state.members.filter(m => m.active).length;
  document.getElementById("metric-members").textContent = activeMembersCount;

  if (s) {
    if (s.unfilled_slots_count && s.unfilled_slots_count > 0) {
      document.getElementById("metric-coverage").textContent = `${s.coverage_pct}%`;
      document.getElementById("metric-coverage-sub").textContent = `${s.unfilled_slots_count} slot(s) unfilled (marked with ❌ X)`;
      document.getElementById("metric-coverage").style.color = "#dc2626";
    } else {
      document.getElementById("metric-coverage").textContent = "100%";
      const totalH = s.total_demand_hours || 140;
      const totalS = Math.round(totalH * 2);
      document.getElementById("metric-coverage-sub").textContent = `${totalS} / ${totalS} slots (${totalH}h)`;
      document.getElementById("metric-coverage").style.color = "";
    }

    document.getElementById("metric-fairness").textContent = `${s.fairness_score}%`;
    document.getElementById("metric-fairness-sub").textContent = `Max diff: ±${s.max_hours_deviation}h (avg ${s.avg_hours_per_member}h)`;

    document.getElementById("metric-repeat").textContent = `${s.repeat_consistency_pct}%`;
    document.getElementById("metric-repeat-sub").textContent = `${s.repeat_matches} / ${s.repeat_candidates} slots repeated`;
  } else {
    document.getElementById("metric-coverage").textContent = "0%";
    document.getElementById("metric-coverage-sub").textContent = "Not yet generated for this week";
    document.getElementById("metric-coverage").style.color = "";
    document.getElementById("metric-fairness").textContent = "--";
    document.getElementById("metric-repeat").textContent = "--";
  }
}

function renderScheduleGrid() {
  const daysHeader = document.getElementById("schedule-days-header");
  const subheaders = document.getElementById("schedule-subheaders");
  const tbody = document.getElementById("schedule-grid-tbody");

  const dates = state.scheduleData?.dates || ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const assignments = state.scheduleData?.assignments || [];

  // Map: `${day},${slot},${house}` -> assignment
  const slotMap = new Map();
  assignments.forEach(a => {
    slotMap.set(`${a.day_of_week},${a.slot},${a.house}`, a);
  });

  // Build Day Headers
  daysHeader.innerHTML = '<th class="th-time" rowspan="2">Time</th>';
  subheaders.innerHTML = "";

  DAYS.forEach((day, dIdx) => {
    const dateLabel = dates[dIdx] || "";
    if (state.houseFilter === "all") {
      daysHeader.innerHTML += `<th colspan="2">${day}<br><span style="font-weight:400; font-size:0.75rem; opacity:0.85;">${dateLabel}</span></th>`;
      subheaders.innerHTML += `<th class="th-burn">Burn</th><th class="th-thc">THC</th>`;
    } else if (state.houseFilter === "Burn") {
      daysHeader.innerHTML += `<th>${day}<br><span style="font-weight:400; font-size:0.75rem; opacity:0.85;">${dateLabel}</span></th>`;
      subheaders.innerHTML += `<th class="th-burn">Burn</th>`;
    } else if (state.houseFilter === "THC") {
      daysHeader.innerHTML += `<th>${day}<br><span style="font-weight:400; font-size:0.75rem; opacity:0.85;">${dateLabel}</span></th>`;
      subheaders.innerHTML += `<th class="th-thc">THC</th>`;
    }
  });

  // Build 24 Rows
  tbody.innerHTML = "";
  for (let s = 0; s < SLOTS_PER_DAY; s++) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="td-time">${slotToTimeStr(s)}</td>`;

    for (let d = 0; d < 7; d++) {
      if (state.houseFilter === "all" || state.houseFilter === "Burn") {
        const burnAssign = slotMap.get(`${d},${s},Burn`);
        tr.appendChild(createSlotCell(burnAssign, d, s, "Burn"));
      }
      if (state.houseFilter === "all" || state.houseFilter === "THC") {
        const thcAssign = slotMap.get(`${d},${s},THC`);
        tr.appendChild(createSlotCell(thcAssign, d, s, "THC"));
      }
    }
    tbody.appendChild(tr);
  }
}

function createSlotCell(assign, day, slot, house) {
  const td = document.createElement("td");
  td.className = "schedule-slot";

  if (assign) {
    const isUnfilled = assign.member_id === "UNFILLED";
    const isHighlighted = state.memberFilter === "all" || state.memberFilter === assign.member_id;
    const pill = document.createElement("div");
    pill.className = `slot-assignment-pill ${isUnfilled ? 'slot-unfilled' : ''}`;
    pill.style.backgroundColor = isUnfilled ? "#dc2626" : (assign.color || "#2563EB");
    pill.style.opacity = isHighlighted ? "1.0" : "0.2";
    pill.textContent = isUnfilled ? "❌ X (UNFILLED)" : (assign.member_name || "Assigned");
    pill.title = isUnfilled
      ? `UNFILLED SLOT: Nobody available!\n${DAYS[day]} ${slotRangeStr(slot)} @ ${house}\nClick to assign a volunteer.`
      : `${assign.member_name} @ ${house}\n${DAYS[day]} ${slotRangeStr(slot)}`;

    // Quick swap / tweak click handler
    pill.addEventListener("click", () => {
      promptSlotEdit(day, slot, house, assign);
    });

    td.appendChild(pill);
  } else {
    td.innerHTML = '<span style="color: #cbd5e1;">—</span>';
  }
  return td;
}

function promptSlotEdit(day, slot, house, currentAssign) {
  const activeMembers = state.members.filter(m => m.active);
  const options = activeMembers.map((m, idx) => `${idx + 1}. ${m.name}`).join("\n");
  const input = prompt(
    `Reassign ${house} house for ${DAYS[day]} at ${slotToTimeStr(slot)}:\nCurrently: ${currentAssign.member_name}\n\nEnter number:\n${options}`,
    "1"
  );
  if (!input) return;
  const num = parseInt(input.trim());
  if (num >= 1 && num <= activeMembers.length) {
    const chosen = activeMembers[num - 1];
    reassignSlot(day, slot, house, chosen.id);
  }
}

async function reassignSlot(day, slot, house, memberId) {
  try {
    const res = await fetch(`/api/schedules/${state.currentWeekId}/slots`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        day_of_week: day,
        slot: slot,
        house: house,
        member_id: memberId,
      }),
    });
    if (res.ok) {
      showToast("Slot reassigned successfully");
      loadSchedule();
    }
  } catch (err) {
    console.error("Reassign error:", err);
  }
}

function renderSummaryTable() {
  const tbody = document.getElementById("summary-table-tbody");
  tbody.innerHTML = "";
  const members = state.scheduleData?.stats?.members || [];

  if (members.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: var(--gray-400); padding: 1.5rem;">No schedule generated yet. Click 'Generate Optimal Schedule' above!</td></tr>`;
    return;
  }

  members.forEach(m => {
    const tr = document.createElement("tr");
    const diffBadge = m.diff_hours === 0 
      ? `<span style="color: var(--success); font-weight: 700;">Exact</span>`
      : `<span style="color: ${m.diff_hours > 0 ? '#2563eb' : '#dc2626'}; font-weight: 700;">${m.diff_hours > 0 ? '+' : ''}${m.diff_hours}h</span>`;
    
    tr.innerHTML = `
      <td style="font-weight: 600; display: flex; align-items: center; gap: 0.5rem;">
        <span class="color-dot" style="background-color: ${m.color};"></span>
        ${m.name}
      </td>
      <td style="font-weight: 700;">${m.assigned_hours} hrs</td>
      <td style="color: var(--gray-500);">${m.target_hours} hrs</td>
      <td>${diffBadge}</td>
      <td>${m.burn_hours} hrs</td>
      <td>${m.thc_hours} hrs</td>
      <td>${m.days_worked} days</td>
      <td>${m.shift_count} shifts</td>
      <td>${m.avg_shift_hours} hrs</td>
    `;
    tbody.appendChild(tr);
  });
}

// ----------------- Master Schedule Painter -----------------

function buildMasterPainterTable() {
  const tbody = document.getElementById("master-painter-tbody");
  tbody.innerHTML = "";

  for (let s = 0; s < SLOTS_PER_DAY; s++) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="td-time">${slotToTimeStr(s)}</td>`;

    for (let d = 0; d < 7; d++) {
      const td = document.createElement("td");
      td.className = "painter-cell";
      td.dataset.day = d;
      td.dataset.slot = s;

      td.addEventListener("mousedown", (e) => {
        e.preventDefault();
        state.isPainting = true;
        const key = `${d},${s}`;
        state.paintMode = state.masterBusySlots.has(key) ? "available" : "busy";
        toggleMasterCell(td, key, state.paintMode);
      });

      td.addEventListener("mouseenter", () => {
        if (state.isPainting) {
          const key = `${d},${s}`;
          toggleMasterCell(td, key, state.paintMode);
        }
      });

      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
}

function toggleMasterCell(td, key, mode) {
  if (mode === "busy") {
    state.masterBusySlots.add(key);
    td.classList.add("cell-busy");
  } else {
    state.masterBusySlots.delete(key);
    td.classList.remove("cell-busy");
  }
}

async function syncMasterPainter() {
  if (!state.selectedMasterMemberId) return;

  try {
    const res = await fetch(`/api/members/${state.selectedMasterMemberId}/master-schedule`);
    const items = await res.json();
    state.masterBusySlots.clear();

    items.forEach(it => {
      for (let s = it.start_slot; s < it.end_slot; s++) {
        state.masterBusySlots.add(`${it.day_of_week},${s}`);
      }
    });

    // Update cells in DOM
    const cells = document.querySelectorAll("#master-painter-tbody .painter-cell");
    cells.forEach(td => {
      const key = `${td.dataset.day},${td.dataset.slot}`;
      if (state.masterBusySlots.has(key)) {
        td.classList.add("cell-busy");
      } else {
        td.classList.remove("cell-busy");
      }
    });
  } catch (err) {
    console.error("Failed to load member master schedule:", err);
  }
}

async function saveMasterSchedule() {
  if (!state.selectedMasterMemberId) return;

  // Convert set of "d,s" into contiguous blocks
  const items = [];
  for (let d = 0; d < 7; d++) {
    let inBlock = false;
    let startSlot = 0;
    for (let s = 0; s < SLOTS_PER_DAY; s++) {
      const isBusy = state.masterBusySlots.has(`${d},${s}`);
      if (isBusy && !inBlock) {
        inBlock = true;
        startSlot = s;
      } else if (!isBusy && inBlock) {
        items.push({
          member_id: state.selectedMasterMemberId,
          day_of_week: d,
          start_slot: startSlot,
          end_slot: s,
          label: "Busy",
        });
        inBlock = false;
      }
    }
    if (inBlock) {
      items.push({
        member_id: state.selectedMasterMemberId,
        day_of_week: d,
        start_slot: startSlot,
        end_slot: SLOTS_PER_DAY,
        label: "Busy",
      });
    }
  }

  try {
    const res = await fetch(`/api/members/${state.selectedMasterMemberId}/master-schedule`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(items),
    });
    if (res.ok) {
      showToast("Master schedule saved!");
    }
  } catch (err) {
    showToast("Error saving master schedule", "error");
  }
}

function clearMasterSchedule() {
  if (confirm("Clear all busy slots in the master schedule for this member?")) {
    state.masterBusySlots.clear();
    document.querySelectorAll("#master-painter-tbody .painter-cell").forEach(td => {
      td.classList.remove("cell-busy");
    });
  }
}

// ----------------- Weekly Overrides Painter -----------------

function buildOverridePainterTable() {
  const tbody = document.getElementById("override-painter-tbody");
  const header = document.getElementById("override-days-header");
  header.innerHTML = '<th class="th-time">Time</th>';
  DAYS.forEach(day => header.innerHTML += `<th>${day}</th>`);

  tbody.innerHTML = "";
  for (let s = 0; s < SLOTS_PER_DAY; s++) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="td-time">${slotToTimeStr(s)}</td>`;

    for (let d = 0; d < 7; d++) {
      const td = document.createElement("td");
      td.className = "painter-cell";
      td.dataset.day = d;
      td.dataset.slot = s;

      td.addEventListener("mousedown", (e) => {
        e.preventDefault();
        state.isPainting = true;
        const key = `${d},${s}`;
        state.paintMode = state.overrideBusySlots.has(key) ? "available" : "busy";
        toggleOverrideCell(td, key, state.paintMode);
      });

      td.addEventListener("mouseenter", () => {
        if (state.isPainting) {
          const key = `${d},${s}`;
          toggleOverrideCell(td, key, state.paintMode);
        }
      });

      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
}

function toggleOverrideCell(td, key, mode) {
  if (mode === "busy") {
    state.overrideBusySlots.add(key);
    td.classList.add("cell-busy");
  } else {
    state.overrideBusySlots.delete(key);
    td.classList.remove("cell-busy");
  }
}

async function syncOverridePainter() {
  if (!state.selectedOverrideMemberId) return;

  try {
    const res = await fetch(`/api/members/${state.selectedOverrideMemberId}/weekly-overrides?week_id=${state.currentWeekId}`);
    const items = await res.json();
    state.overrideBusySlots.clear();

    items.forEach(it => {
      for (let s = it.start_slot; s < it.end_slot; s++) {
        state.overrideBusySlots.add(`${it.day_of_week},${s}`);
      }
    });

    const cells = document.querySelectorAll("#override-painter-tbody .painter-cell");
    cells.forEach(td => {
      const key = `${td.dataset.day},${td.dataset.slot}`;
      if (state.overrideBusySlots.has(key)) {
        td.classList.add("cell-busy");
      } else {
        td.classList.remove("cell-busy");
      }
    });
  } catch (err) {
    console.error("Failed to load overrides:", err);
  }
}

async function saveWeeklyOverrides() {
  if (!state.selectedOverrideMemberId) return;

  const items = [];
  for (let d = 0; d < 7; d++) {
    let inBlock = false;
    let startSlot = 0;
    for (let s = 0; s < SLOTS_PER_DAY; s++) {
      const isBusy = state.overrideBusySlots.has(`${d},${s}`);
      if (isBusy && !inBlock) {
        inBlock = true;
        startSlot = s;
      } else if (!isBusy && inBlock) {
        items.push({
          week_id: state.currentWeekId,
          member_id: state.selectedOverrideMemberId,
          day_of_week: d,
          start_slot: startSlot,
          end_slot: s,
          override_type: "busy",
          note: "Weekly Exception",
        });
        inBlock = false;
      }
    }
    if (inBlock) {
      items.push({
        week_id: state.currentWeekId,
        member_id: state.selectedOverrideMemberId,
        day_of_week: d,
        start_slot: startSlot,
        end_slot: SLOTS_PER_DAY,
        override_type: "busy",
        note: "Weekly Exception",
      });
    }
  }

  try {
    const res = await fetch(`/api/members/${state.selectedOverrideMemberId}/weekly-overrides?week_id=${state.currentWeekId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(items),
    });
    if (res.ok) {
      showToast("Weekly exceptions saved!");
    }
  } catch (err) {
    showToast("Error saving exceptions", "error");
  }
}

function clearWeeklyOverrides() {
  if (confirm("Clear all weekly exceptions for this member?")) {
    state.overrideBusySlots.clear();
    document.querySelectorAll("#override-painter-tbody .painter-cell").forEach(td => {
      td.classList.remove("cell-busy");
    });
  }
}

// ----------------- Roster Settings -----------------

function renderRosterCards() {
  const container = document.getElementById("roster-cards-container");
  container.innerHTML = "";

  state.members.forEach(m => {
    const card = document.createElement("div");
    card.className = "member-card";
    if (!m.active) {
      card.style.opacity = "0.7";
      card.style.background = "#f8fafc";
    }

    card.innerHTML = `
      <div class="member-header">
        <div class="member-name">
          <span class="color-dot" style="background-color: ${m.color};"></span>
          ${m.name}
          ${!m.active ? '<span style="font-size: 0.7rem; color: #dc2626; border: 1px solid #fca5a5; padding: 0.1rem 0.4rem; border-radius: 4px;">Inactive</span>' : ''}
        </div>
        <button class="btn btn-secondary btn-sm" onclick="openMemberModal('${m.id}')">Edit</button>
      </div>

      <div style="display: flex; justify-content: space-between; font-size: 0.8rem; color: var(--gray-600); border-top: 1px solid var(--gray-200); padding-top: 0.5rem; margin-top: 0.5rem;">
        <span>Weight: <strong>${m.weight}x</strong></span>
        <span>Target: <strong>${m.active ? (140 / state.members.filter(x => x.active).length).toFixed(1) : 0} hrs/wk</strong></span>
      </div>
    `;
    container.appendChild(card);
  });
}

window.openMemberModal = function(memberId) {
  const modal = document.getElementById("modal-member");
  const delBtn = document.getElementById("btn-delete-member");
  
  if (memberId) {
    const m = state.members.find(x => x.id === memberId);
    if (!m) return;
    document.getElementById("member-modal-title").textContent = `Edit ${m.name}`;
    document.getElementById("edit-member-id").value = m.id;
    document.getElementById("edit-member-name").value = m.name;
    document.getElementById("edit-member-email").value = m.email || "";
    document.getElementById("edit-member-weight").value = m.weight;
    document.getElementById("edit-member-color").value = m.color;
    document.getElementById("edit-member-active").checked = m.active;
    delBtn.style.display = "block";
  } else {
    document.getElementById("member-modal-title").textContent = "Add New Member";
    document.getElementById("edit-member-id").value = "";
    document.getElementById("edit-member-name").value = "";
    document.getElementById("edit-member-email").value = "";
    document.getElementById("edit-member-weight").value = "1.0";
    document.getElementById("edit-member-color").value = "#2563EB";
    document.getElementById("edit-member-active").checked = true;
    delBtn.style.display = "none";
  }
  modal.style.display = "flex";
};

function closeMemberModal() {
  document.getElementById("modal-member").style.display = "none";
}

async function handleMemberFormSubmit(e) {
  e.preventDefault();
  const id = document.getElementById("edit-member-id").value;
  const memberData = {
    name: document.getElementById("edit-member-name").value.trim(),
    email: document.getElementById("edit-member-email").value.trim(),
    shift_preference: "daily_short",
    weight: parseFloat(document.getElementById("edit-member-weight").value) || 1.0,
    color: document.getElementById("edit-member-color").value,
    active: document.getElementById("edit-member-active").checked,
  };

  try {
    if (id) {
      memberData.id = id;
      await fetch(`/api/members/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(memberData),
      });
      showToast(`Updated ${memberData.name}`);
    } else {
      await fetch("/api/members", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(memberData),
      });
      showToast(`Added ${memberData.name}`);
    }
    closeMemberModal();
    await loadMembers();
  } catch (err) {
    showToast("Failed to save member", "error");
  }
}

async function handleMemberDelete() {
  const id = document.getElementById("edit-member-id").value;
  const name = document.getElementById("edit-member-name").value;
  if (!id) return;

  if (confirm(`Are you sure you want to permanently delete ${name}?`)) {
    try {
      await fetch(`/api/members/${id}`, { method: "DELETE" });
      showToast(`Deleted ${name}`);
      closeMemberModal();
      await loadMembers();
    } catch (err) {
      showToast("Failed to delete member", "error");
    }
  }
}

// ----------------- Generate Schedule Modal -----------------

function openGenerateModal() {
  document.getElementById("gen-week-id").value = state.currentWeekId;
  document.getElementById("modal-generate").style.display = "flex";
}

function closeGenerateModal() {
  document.getElementById("modal-generate").style.display = "none";
}

async function runScheduleGeneration() {
  const btn = document.getElementById("btn-run-gen");
  btn.disabled = true;
  btn.textContent = "Optimizing with CP-SAT...";

  const repeatConsistency = document.getElementById("gen-repeat-consistency").checked;
  const minShiftHours = parseFloat(document.getElementById("gen-min-shift").value);

  try {
    const res = await fetch("/api/schedules/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        week_id: state.currentWeekId,
        use_previous_week_consistency: repeatConsistency,
        min_shift_hours: minShiftHours,
      }),
    });
    const result = await res.json();

    if (result.success) {
      showToast(`Generated optimal schedule for ${state.currentWeekId}! Fairness: ${result.stats.fairness_score}%`);
      closeGenerateModal();
      await loadSchedule();
    } else {
      showToast(`Generation failed: ${result.message || 'Conflicts detected'}`, "error");
    }
  } catch (err) {
    showToast("Error connecting to optimization server", "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = "<span>🚀</span> Run Optimizer";
  }
}

// ----------------- Google Sheets & Spreadsheet Export -----------------

async function copyForGoogleSheets() {
  try {
    const res = await fetch(`/api/schedules/${state.currentWeekId}/export.tsv`);
    if (!res.ok) {
      showToast("No schedule generated to copy! Click 'Generate Optimal Schedule' first.", "error");
      return;
    }
    const tsvText = await res.text();
    await navigator.clipboard.writeText(tsvText);
    showToast("Copied to clipboard! In Google Sheets, click cell A1 and press Ctrl+V (Cmd+V) to paste.");
  } catch (err) {
    showToast("Clipboard copy failed. Try downloading CSV instead.", "error");
  }
}
