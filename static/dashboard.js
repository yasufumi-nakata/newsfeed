"use strict";
const $ = id => document.getElementById(id);
let dashboard = null, view = "today", busy = false, epoch = 0, requestSerial = 0;
let selectedQuest = null, toastTimer = null, lastRenderedDay = null;
const questDrafts = new Map();
const QUEST_DISPLAY_ORDER = new Map([
  "listening", "speaking", "reading", "writing", "research", "business", "work",
  "math", "walk", "pushups", "squats"
].map((id, index) => [id, index]));
const TASK_ROTATION_MS = 15000;
let taskOrder = [], rotationEnabled = true, nextRotationAt = Date.now() + TASK_ROTATION_MS;
let taskInteractionUntil = 0;
const dateFormatter = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit" });
const timeFormatter = new Intl.DateTimeFormat("ja-JP", { timeZone: "Asia/Tokyo", hour: "2-digit", minute: "2-digit" });
const dateInTokyo = date => dateFormatter.format(date);
function el(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
function notify(message, error = false) { clearTimeout(toastTimer); $("action-status").textContent = message; $("action-status").classList.toggle("error", error); $("action-status").hidden = false; toastTimer = setTimeout(() => { $("action-status").hidden = true; }, error ? 10000 : 3500); }
function setBusy(value) { busy = value; document.querySelectorAll(".quest-actions button, .quest-actions input, .complete-checkbox, .check-field input, #refresh").forEach(node => { node.disabled = value; }); }
async function api(path, body) {
  const options = { cache: "no-store", signal: AbortSignal.timeout(45000) };
  if (body !== undefined) Object.assign(options, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) { const error = new Error(payload.error || "保存できませんでした。もう一度お試しください。"); error.status = response.status; throw error; }
  return payload;
}
function renderOverview() {
  const p = dashboard.progress;
  document.documentElement.dataset.stage = p.stage || "normal";
  document.querySelector('meta[name="theme-color"]').content = p.bonus_unlocked ? "#251117" : "#0b1627";
  $("stage-heading").textContent = p.bonus_unlocked ? "ボーナスステージ" : "今日のクエスト";
  $("quests-heading").textContent = p.bonus_unlocked ? "ボーナスクエスト" : "デイリークエスト";
  $("stage-note").textContent = p.bonus_unlocked ? "通常11項目クリア · 追加達成で +10 XP" : "通常目標で +10 XP";
  $("quest-count-label").textContent = p.bonus_unlocked ? "ボーナス達成" : "通常クエスト達成";
  $("level").textContent = p.level;
  $("total-xp").textContent = `累計 ${p.total_xp.toLocaleString()} XP`;
  $("level-progress").value = p.level_progress;
  $("level-progress").max = p.level_span;
  $("next-level").textContent = `Lv.${p.level + 1} まで ${p.level_span - p.level_progress} XP`;
  $("today-xp").replaceChildren(document.createTextNode(`${p.today_xp} `), el("small", "", "XP"));
  $("quest-count").replaceChildren(document.createTextNode(`${p.bonus_unlocked ? p.bonus_done : p.quest_done} `), el("small", "", "/ 11"));
  $("todoist-count").replaceChildren(document.createTextNode(`${p.todoist_completed.length} `), el("small", "", "件"));
  $("xp-breakdown").textContent = `クエスト ${p.quest_xp} ＋ Todoist ${p.todoist_xp}`;
  $("day-label").textContent = `DAY ${p.day_number} · Asia/Tokyo`;
  $("source-link").href = p.source_url;
}
function renderQuests() {
  const fragment = document.createDocumentFragment();
  const quests = [...dashboard.progress.quests].sort((a, b) =>
    (QUEST_DISPLAY_ORDER.get(a.id) ?? QUEST_DISPLAY_ORDER.size) - (QUEST_DISPLAY_ORDER.get(b.id) ?? QUEST_DISPLAY_ORDER.size));
  for (const q of quests) {
    const row = el("div", `quest-row ${q.status === "normal" ? "done" : q.status}`); row.dataset.questId = q.id;
    const info = el("div", "quest-info");
    const name = el("button", "quest-name"); name.type = "button"; name.setAttribute("aria-label", `${q.name}の目標・昇格設定`);
    name.append(el("span", "name", q.name), el("span", "quest-level", `Lv.${q.level}`));
    name.title = `${q.gp} / 20 GP · ボーナス ${q.bonus_days} / 3日。押すと目標・成長の詳細を表示`;
    name.addEventListener("click", () => { selectedQuest = q.id; renderQuestDialog(); $("quest-dialog").showModal(); });
    if (q.promotion_pending) name.append(el("span", "quest-flag", `明日 Lv.${q.next_level}`));
    else if (q.at_cap) name.append(el("span", "quest-flag", "上限"));
    else if (q.hold) name.append(el("span", "quest-flag", "増量保留"));
    else if (q.category === "身体" && q.status === "bonus" && !q.safe_bonus) name.append(el("span", "quest-flag", "体調確認"));
    info.append(name);
    const goals = el("div", "quest-goals");
    goals.setAttribute("aria-label", `${q.name}の実績 ${q.amount}${q.unit} / 目標 ${q.target}${q.unit}`);
    goals.append(el("span", "current-amount", q.amount), el("span", "slash", "/"), el("span", "target-amount", q.target), el("small", "", q.unit));
    const actions = el("form", "quest-actions"); actions.setAttribute("aria-label", `${q.name}の今日の記録`);
    const field = el("label", "amount-field");
    const input = el("input", "quest-amount"); input.type = "number"; input.min = "0"; input.max = "1000000";
    input.step = q.unit === "km" ? "any" : "1"; input.inputMode = q.unit === "km" ? "decimal" : "numeric";
    input.required = true; input.dataset.entry = "amount";
    input.value = questDrafts.has(q.id) ? questDrafts.get(q.id) : String(q.amount);
    input.setAttribute("aria-label", `${q.name}の今日の合計（${q.unit}）`);
    input.addEventListener("input", () => questDrafts.set(q.id, input.value));
    field.append(input, el("span", "amount-unit", q.unit));
    const save = el("button", "record-button", "記録"); save.type = "submit"; save.dataset.action = "save";
    save.setAttribute("aria-label", `${q.name}を記録`);
    const add = el("button", "increment-button", `+${q.step}${q.unit}`); add.type = "button"; add.dataset.action = "increment";
    add.setAttribute("aria-label", `${q.name}を${q.step}${q.unit}追加`);
    actions.addEventListener("submit", event => { event.preventDefault(); if (actions.reportValidity()) saveQuest(q.id, { amount: input.valueAsNumber }); });
    add.addEventListener("click", () => { if (actions.reportValidity()) saveQuest(q.id, { amount: Math.round((input.valueAsNumber + q.step) * 1000) / 1000 }); });
    actions.append(field, save, add);
    row.append(info, goals, actions); fragment.append(row);
  }
  $("quests").replaceChildren(fragment);
}
function dueDay(task) {
  if (!task.due) return null;
  if (!task.due.includes("T")) return task.due.slice(0, 10);
  // Todoist dates without a zone are wall-clock times in the configured timezone.
  const value = /(?:Z|[+-]\d\d:\d\d)$/.test(task.due) ? task.due : `${task.due}+09:00`;
  const parsed = new Date(value); return Number.isNaN(parsed.valueOf()) ? task.due.slice(0,10) : dateInTokyo(parsed);
}
function overdue(task) {
  const day = dueDay(task); if (!day) return false;
  if (day < dashboard.progress.day) return true;
  if (task.due.includes("T")) { const value = /(?:Z|[+-]\d\d:\d\d)$/.test(task.due) ? task.due : `${task.due}+09:00`; return new Date(value).valueOf() < Date.now(); }
  return false;
}
function renderTasks() {
  const today = dashboard.progress.day;
  const filtered = dashboard.tasks.filter(t => view === "all" || (dueDay(t) && dueDay(t) <= today));
  const byId = new Map(filtered.map(task => [task.id, task]));
  const known = new Set(taskOrder);
  // Keep the rotating order through polls, quest saves and completion syncs.
  taskOrder = [...taskOrder.filter(id => byId.has(id)), ...filtered.filter(task => !known.has(task.id)).map(task => task.id)];
  const tasks = taskOrder.map(id => byId.get(id));
  const scrollTop = $("task-scroll").scrollTop;
  const focused = document.activeElement;
  const focusTaskId = focused.closest?.(".task")?.dataset.taskId;
  const focusSelector = focused.matches?.(".complete-checkbox") ? ".complete-checkbox" : ".task-link";
  $("task-count").textContent = tasks.length;
  $("task-summary").textContent = tasks.length ? `残り ${tasks.length} 件` : "";
  $("view-today").classList.toggle("active", view === "today"); $("view-today").setAttribute("aria-pressed", String(view === "today"));
  $("view-all").classList.toggle("active", view === "all"); $("view-all").setAttribute("aria-pressed", String(view === "all"));
  const fragment = document.createDocumentFragment();
  for (const task of tasks) {
    const row = el("article", `task priority-${task.priority}`); row.dataset.taskId = task.id;
    const control = el("label", "complete-control");
    const checkbox = el("input", "complete-checkbox"); checkbox.type = "checkbox"; checkbox.dataset.taskId = task.id;
    checkbox.setAttribute("aria-label", `${task.title}を完了して10 XP獲得`); checkbox.addEventListener("change", () => completeTask(task)); control.append(checkbox);
    const content = el("div", "task-main"); const link = el("a", "task-link", task.title); link.href = `https://app.todoist.com/app/task/${encodeURIComponent(task.id)}`; link.target = "_blank"; link.rel = "noopener noreferrer"; content.append(link);
    if (task.description) { const desc = el("p", "task-description", task.description); desc.title = task.description; content.append(desc); }
    const meta = el("div", "task-details"); const project = el("span", "", task.project); project.prepend(el("span", "project-dot", "●")); meta.append(project);
    const day = dueDay(task);
    if (day) { let label = day === today ? "今日" : day; if (overdue(task)) label += " · 期限切れ"; if (task.due.includes("T")) { const time = /(?:Z|[+-]\d\d:\d\d)$/.test(task.due) ? task.due : `${task.due}+09:00`; label += ` ${timeFormatter.format(new Date(time))}`; } meta.append(el("span", overdue(task) ? "overdue" : "", label)); }
    if (task.is_recurring) meta.append(el("span", "", "↻ 繰り返し"));
    if (task.priority >= 3) meta.append(el("span", "", `P${5-task.priority}`));
    for (const label of task.labels || []) meta.append(el("span", "", `#${label}`));
    content.append(meta); row.append(control, content); fragment.append(row);
  }
  if (!tasks.length) { const empty = el("div", "empty"); const unavailable = dashboard.errors.length && !dashboard.updated_at; empty.append(el("strong", "", unavailable ? "Todoist を取得できませんでした" : "今日のタスクはクリア！"), document.createTextNode(unavailable ? "接続を確認して「更新」を押してください。クエストは記録できます。" : "次の一歩は、左のクエストから。")); fragment.append(empty); }
  $("tasks").replaceChildren(fragment);
  $("task-scroll").scrollTop = scrollTop;
  if (focusTaskId) $("tasks").querySelector(`[data-task-id="${CSS.escape(focusTaskId)}"] ${focusSelector}`)?.focus({ preventScroll: true });
  const done = dashboard.progress.todoist_completed;
  $("completed-section").hidden = !done.length;
  $("completed-summary").textContent = `今日の完了 ${done.length} 件 · ${dashboard.progress.todoist_xp} XP`;
  $("completed-tasks").replaceChildren(...done.map(t => { const row = el("div", "completed-task"); row.append(el("span", "", `✓ ${t.title}`), el("span", "xp", `+${t.xp} XP`)); return row; }));
  renderRotationStatus();
}
function rotationPauseReason(now = Date.now()) {
  if (!rotationEnabled) return "自動切替を一時停止中";
  if (document.hidden) return "非表示中は切替を停止";
  if (busy || document.querySelector("dialog[open]")) return "操作中は切替を停止";
  if ($("task-scroll").scrollTop > 2) return "先頭に戻ると自動切替";
  if ((document.activeElement !== $("task-scroll") && $("task-scroll").contains(document.activeElement)) || now < taskInteractionUntil) return "操作中は切替を停止";
  return "";
}
function renderRotationStatus(now = Date.now()) {
  const paused = rotationPauseReason(now);
  $("rotation-status").textContent = paused || (taskOrder.length > 1 ? `自動切替 · あと${Math.max(0, Math.ceil((nextRotationAt - now) / 1000))}秒` : "表示中のタスクはすべて表示済み");
  $("toggle-rotation").textContent = rotationEnabled ? "一時停止" : "再開";
  $("toggle-rotation").setAttribute("aria-pressed", String(rotationEnabled));
}
function advanceTaskStream(now = Date.now()) {
  if (rotationPauseReason(now)) {
    nextRotationAt = now + TASK_ROTATION_MS;
  } else if (dashboard && taskOrder.length > 1 && now >= nextRotationAt) {
    taskOrder.push(taskOrder.shift());
    nextRotationAt = now + TASK_ROTATION_MS;
    renderTasks();
    setBusy(busy);
  }
  renderRotationStatus(now);
}
function changeTaskView(nextView) {
  view = nextView;
  taskOrder = [];
  $("task-scroll").scrollTop = 0;
  nextRotationAt = Date.now() + TASK_ROTATION_MS;
  if (dashboard) { renderTasks(); setBusy(busy); }
}
function renderConnection() {
  const errors = [...dashboard.errors, ...dashboard.activity_errors];
  $("connection-status").classList.toggle("error", errors.length > 0);
  $("connection-status").textContent = errors.length ? errors.join(" / ") : `自動同期中 · タスク ${dashboard.updated_at ? timeFormatter.format(new Date(dashboard.updated_at)) : "—"} / 完了履歴 ${dashboard.progress.todoist_synced_at ? timeFormatter.format(new Date(dashboard.progress.todoist_synced_at)) : "未同期"}`;
}
function render() {
  if (!dashboard?.progress) return;
  if (lastRenderedDay && lastRenderedDay !== dashboard.progress.day) questDrafts.clear();
  lastRenderedDay = dashboard.progress.day;
  const focused = document.activeElement;
  const focusedQuest = focused.closest?.("[data-quest-id]")?.dataset.questId;
  const focusedEntry = focused.dataset?.entry;
  const focusedAction = focused.dataset?.action;
  renderOverview(); renderQuests(); renderTasks(); renderConnection();
  if (selectedQuest && $("quest-dialog").open) renderQuestDialog();
  setBusy(busy);
  if (focusedQuest && (focusedEntry || focusedAction)) document.querySelector(`[data-quest-id="${focusedQuest}"] [data-${focusedEntry ? "entry" : "action"}="${focusedEntry || focusedAction}"]`)?.focus({ preventScroll: true });
}
function renderQuestDialog() {
  const q = dashboard.progress.quests.find(q => q.id === selectedQuest); if (!q) return;
  $("quest-dialog-title").textContent = `${q.name} · Lv.${q.level}`;
  $("quest-description").textContent = q.description;
  $("quest-targets").textContent = `今日の実績 ${q.amount}${q.unit} / ${dashboard.progress.bonus_unlocked ? "ボーナス" : "通常"}目標 ${q.target}${q.unit}`;
  $("quest-growth").textContent = q.promotion_pending ? `明日から Lv.${q.next_level}。ボーナス目標は ${q.next_bonus_target}${q.unit} になります。` : `${q.gp} / 20 GP · このレベルでボーナス ${q.bonus_days} / 3日`;
  $("safe-field").hidden = q.category !== "身体"; $("safe-bonus").checked = q.safe_bonus; $("hold-growth").checked = q.hold;
}
async function saveQuest(id, patch) {
  if (busy || !dashboard) return;
  const q = dashboard.progress.quests.find(q => q.id === id);
  epoch++; setBusy(true);
  try {
    const wasBonus = dashboard.progress.bonus_unlocked;
    // Quantity entry also clears a legacy rest flag so old records stay editable.
    const result = await api(`/api/quests/${id}`, { day: dashboard.progress.day, expected_amount: q.amount, ...(Object.hasOwn(patch, "amount") ? { rest: false } : {}), ...patch });
    const difference = result.progress.total_xp - dashboard.progress.total_xp;
    if (Object.hasOwn(patch, "amount")) questDrafts.delete(id);
    dashboard.progress = result.progress; render();
    const saved = result.progress.quests.find(quest => quest.id === id);
    notify(!wasBonus && result.progress.bonus_unlocked ? "通常11項目を達成！ ボーナスステージへ" : `${q.name}${Object.hasOwn(patch, "amount") ? ` ${saved.amount}${q.unit}` : ""}を保存${difference ? ` · ${difference > 0 ? "+" : ""}${difference} XP` : ""}`);
  } catch (error) {
    if (error.status === 409 || dashboard.progress.day !== dateInTokyo(new Date())) {
      try { dashboard = await api("/api/dashboard"); } catch { /* Preserve the last available state. */ }
    }
    render(); notify(error.message, true);
  }
  finally { epoch++; setBusy(false); }
}
async function completeTask(task) {
  if (busy) return; epoch++; setBusy(true);
  try {
    const previous = dashboard.progress.total_xp;
    const result = await api(`/api/tasks/${encodeURIComponent(task.id)}/complete`, {});
    dashboard.tasks = dashboard.tasks.filter(t => !result.completed_ids.includes(t.id));
    if (result.progress) dashboard.progress = result.progress;
    dashboard.updated_at = result.updated_at;
    render(); const gained = dashboard.progress.total_xp - previous;
    notify(result.warning || `タスクを完了しました${gained > 0 ? ` · +${gained} XP` : " · 今日の経験値は獲得済み"}`, Boolean(result.warning));
  } catch (error) { render(); notify(error.message, true); }
  finally { epoch++; setBusy(false); }
}
async function load(manual = false) {
  if (busy) return;
  if (manual) { epoch++; setBusy(true); }
  const atEpoch = epoch, serial = ++requestSerial;
  try {
    const data = await api(manual ? "/api/refresh" : "/api/dashboard", manual ? {} : undefined);
    if (atEpoch !== epoch || serial !== requestSerial) return;
    if (!data.progress) throw new Error("クエスト機能を読み込めません。サーバーの状態を確認してください。");
    dashboard = data; render();
    if (manual) notify(data.errors.length || data.activity_errors.length ? "同期に失敗した項目があります。保存済みの記録を表示しています。" : "最新の状態に更新しました", Boolean(data.errors.length || data.activity_errors.length));
  } catch (error) {
    if (atEpoch === epoch) { $("connection-status").textContent = "接続できません。保存済みの表示を保ち、自動で再接続します。"; $("connection-status").classList.add("error"); if (manual) notify(error.message, true); }
  } finally { if (manual) { epoch++; setBusy(false); } }
}
function clock() {
  const now = new Date();
  $("clock").textContent = new Intl.DateTimeFormat("ja-JP", { timeZone: "Asia/Tokyo", month: "long", day: "numeric", weekday: "short", hour: "2-digit", minute: "2-digit" }).format(now);
  if (lastRenderedDay && dateInTokyo(now) !== lastRenderedDay && !busy) load();
}
$("refresh").addEventListener("click", () => load(true));
$("view-today").addEventListener("click", () => changeTaskView("today"));
$("view-all").addEventListener("click", () => changeTaskView("all"));
$("toggle-rotation").addEventListener("click", () => {
  rotationEnabled = !rotationEnabled;
  nextRotationAt = Date.now() + TASK_ROTATION_MS;
  renderRotationStatus();
});
$("task-scroll").addEventListener("scroll", () => {
  nextRotationAt = Date.now() + TASK_ROTATION_MS;
  renderRotationStatus();
}, { passive: true });
for (const event of ["pointerdown", "keydown"]) {
  $("task-scroll").addEventListener(event, () => { taskInteractionUntil = Date.now() + TASK_ROTATION_MS; });
}
window.addEventListener("scroll", () => {
  taskInteractionUntil = Date.now() + TASK_ROTATION_MS;
}, { passive: true });
try {
  const size = localStorage.getItem("ownnewsfeed-text-size");
  if (size === "extra") $("text-size").value = size;
} catch { /* Default sizing also works when local storage is disabled. */ }
document.documentElement.dataset.textSize = $("text-size").value;
$("text-size").addEventListener("change", () => {
  document.documentElement.dataset.textSize = $("text-size").value;
  try { localStorage.setItem("ownnewsfeed-text-size", $("text-size").value); } catch { /* Optional preference. */ }
});
$("safe-bonus").addEventListener("change", () => saveQuest(selectedQuest, { safe_bonus: $("safe-bonus").checked }));
$("hold-growth").addEventListener("change", () => saveQuest(selectedQuest, { hold: $("hold-growth").checked }));
$("show-rules").addEventListener("click", () => $("rules-dialog").showModal());
document.querySelectorAll(".close-dialog").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
document.addEventListener("visibilitychange", () => { if (!document.hidden) { clock(); load(); } });
clock(); load();
setInterval(clock, 1000);
setInterval(advanceTaskStream, 1000);
setInterval(() => { if (!document.hidden) load(); }, 15000);
