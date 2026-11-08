#!/usr/bin/env python3
"""Serve Todoist tasks in an auto-updating signage view."""

import argparse
import json
import os
import re
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import unquote, urlsplit

from todoist_api import TodoistError, TodoistTask, collect_tasks, complete_task, collect_activity, current_user_id
from quest_store import QuestStore, QuestConflict

STATIC_DIR = Path(__file__).resolve().parent / "static"
if not STATIC_DIR.is_dir():
    STATIC_DIR = resources.files("ownnewsfeed_assets")


HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Todoist Task Stream</title>
  <style>
    :root {
      --bg: #171412;
      --bg-2: #211a18;
      --surface: rgba(255, 250, 243, 0.08);
      --surface-2: rgba(255, 250, 243, 0.12);
      --line: rgba(255, 250, 243, 0.20);
      --text: #fffaf3;
      --muted: #cdbfb5;
      --todoist: #e44332;
      --green: #58c184;
      --amber: #f2b84b;
      --blue: #6ea7ff;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      height: 100vh;
      height: 100dvh;
      background: linear-gradient(135deg, var(--bg), var(--bg-2) 58%, #101414);
      color: var(--text);
      font-family: "SF Pro Text", "Hiragino Sans", "Noto Sans JP", "Segoe UI", sans-serif;
      text-rendering: optimizeLegibility;
      -webkit-font-smoothing: antialiased;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      overflow: hidden;
    }

    header {
      padding: 1rem 1.35rem 0.92rem;
      border-bottom: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.24);
      display: grid;
      gap: 0.56rem;
    }

    .title-row {
      display: flex;
      justify-content: space-between;
      gap: 1rem;
      align-items: baseline;
    }

    .title {
      font-size: clamp(1.45rem, 3.3vw, 2.45rem);
      font-weight: 760;
      letter-spacing: 0;
    }

    .title::before {
      content: "";
      display: inline-block;
      width: 0.72em;
      height: 0.72em;
      margin-right: 0.42rem;
      border-radius: 4px;
      background: var(--todoist);
      vertical-align: -0.03em;
    }

    .clock {
      font-size: clamp(1.1rem, 2.2vw, 1.75rem);
      color: var(--muted);
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
      font-weight: 650;
    }

    .meta-row {
      display: flex;
      gap: 0.55rem;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: clamp(0.88rem, 1.18vw, 1.06rem);
      font-weight: 580;
    }

    .pill {
      padding: 0.36rem 0.58rem;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      white-space: nowrap;
    }

    #project-count { color: var(--green); }
    #priority-count { color: var(--todoist); }
    #queue-count { color: var(--amber); }
    #status {
      max-width: 100%;
      white-space: normal;
      overflow-wrap: anywhere;
    }

    #action-status {
      white-space: normal;
      overflow-wrap: anywhere;
      color: var(--green);
    }

    #action-status.error { color: var(--todoist); }

    main {
      min-height: 0;
      min-width: 0;
      overflow-x: hidden;
      overflow-y: auto;
      padding: 0.88rem 1.12rem 1rem;
    }

    .stream {
      min-height: 100%;
      display: grid;
      gap: 0.72rem;
      align-content: start;
    }

    .task {
      display: grid;
      grid-template-columns: 0.7rem minmax(0, 1fr) auto;
      column-gap: 0.82rem;
      row-gap: 0.44rem;
      min-height: 154px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0.86rem 0.95rem;
      text-decoration: none;
      color: inherit;
      background: var(--surface);
      box-shadow: 0 12px 28px rgba(0, 0, 0, 0.24);
      transition: transform 180ms ease, border-color 180ms ease, background 180ms ease;
      animation: task-in 300ms ease both;
    }

    .task:hover {
      transform: translateY(-2px);
      border-color: rgba(228, 67, 50, 0.72);
      background: var(--surface-2);
    }

    .task.new {
      border-color: rgba(88, 193, 132, 0.9);
      box-shadow: 0 0 0 1px rgba(88, 193, 132, 0.28), 0 12px 28px rgba(0, 0, 0, 0.24);
    }

    @keyframes task-in {
      from {
        opacity: 0;
        transform: translateY(-7px);
      }
      to {
        opacity: 1;
        transform: translateY(0);
      }
    }

    .priority-bar {
      grid-column: 1;
      grid-row: 1;
      width: 0.28rem;
      min-height: 100%;
      border-radius: 8px;
      background: #8a8f98;
    }

    .priority-4 .priority-bar { background: var(--todoist); }
    .priority-3 .priority-bar { background: #e26d33; }
    .priority-2 .priority-bar { background: var(--blue); }

    .task-link {
      grid-column: 2;
      min-width: 0;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      grid-template-rows: auto auto 1fr auto;
      column-gap: 0.82rem;
      row-gap: 0.44rem;
      color: inherit;
      text-decoration: none;
    }

    .task-link:focus-visible,
    .complete-control:focus-within {
      outline: 2px solid var(--blue);
      outline-offset: 4px;
      border-radius: 4px;
    }

    .complete-control {
      grid-column: 3;
      grid-row: 1;
      display: flex;
      align-items: center;
      align-self: start;
      gap: 0.4rem;
      min-height: 44px;
      padding: 0 0.25rem;
      color: var(--muted);
      font-size: 0.86rem;
      cursor: pointer;
    }

    .complete-checkbox {
      width: 1.4rem;
      height: 1.4rem;
      margin: 0;
      accent-color: var(--todoist);
      cursor: inherit;
    }

    .task.completing .task-link { opacity: 0.6; }
    .task.completing .complete-control { cursor: wait; }

    .project {
      min-width: 0;
      color: var(--amber);
      font-size: clamp(0.86rem, 1.1vw, 1rem);
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .priority {
      color: var(--muted);
      font-size: clamp(0.82rem, 1vw, 0.95rem);
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }

    .task-title {
      grid-column: 1 / -1;
      font-size: clamp(1.22rem, 2vw, 1.85rem);
      line-height: 1.22;
      font-weight: 760;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }

    .description {
      grid-column: 1 / -1;
      color: #f0e4da;
      font-size: clamp(0.98rem, 1.28vw, 1.18rem);
      line-height: 1.34;
      font-weight: 500;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }

    .task-meta {
      grid-column: 1 / -1;
      color: var(--muted);
      font-size: clamp(0.9rem, 1.1vw, 1rem);
      line-height: 1.35;
      font-weight: 560;
      display: flex;
      align-items: center;
      gap: 0.45rem;
      flex-wrap: wrap;
      min-width: 0;
    }

    .due {
      color: var(--green);
      font-variant-numeric: tabular-nums;
    }

    .overdue .due { color: var(--todoist); }

    .tag {
      max-width: 15rem;
      padding: 0.2rem 0.42rem;
      border-radius: 6px;
      color: #f7efe8;
      background: rgba(255, 250, 243, 0.10);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .empty {
      border: 1px dashed var(--line);
      border-radius: 8px;
      min-height: 200px;
      display: grid;
      place-items: center;
      color: var(--muted);
      font-size: clamp(1rem, 1.35vw, 1.2rem);
      background: rgba(255, 250, 243, 0.06);
      text-align: center;
      padding: 1rem;
    }

    @media (max-width: 980px) {
      header {
        padding: 0.82rem 0.9rem 0.72rem;
      }
      main {
        padding: 0.68rem 0.75rem 0.8rem;
      }
      .task {
        min-height: 142px;
        padding: 0.74rem 0.78rem;
      }
      .task-title {
        -webkit-line-clamp: 3;
      }
    }

    @media (max-width: 640px) {
      .title-row {
        align-items: start;
      }
      .title {
        font-size: clamp(1.16rem, 5vw, 1.55rem);
      }
      .clock {
        font-size: 0.95rem;
      }
      .meta-row {
        gap: 0.42rem;
        font-size: 0.82rem;
      }
      .task {
        grid-template-columns: 0.58rem minmax(0, 1fr) auto;
        min-height: 132px;
      }
      .description {
        -webkit-line-clamp: 2;
      }
      .complete-control {
        flex-direction: column;
        justify-content: center;
        font-size: 0.76rem;
      }
      .tag {
        max-width: 9rem;
      }
    }
  </style>
</head>
<body>
  <header>
    <div class="title-row">
      <div class="title">Todoist Task Stream</div>
      <div class="clock" id="clock">--:--:--</div>
    </div>
    <div class="meta-row">
      <span class="pill" id="project-count">0 projects</span>
      <span class="pill" id="task-count">0 tasks</span>
      <span class="pill" id="priority-count">0 high priority</span>
      <span class="pill" id="queue-count">queue 0</span>
      <span class="pill" id="status">Waiting for first update...</span>
      <span class="pill" id="action-status" role="status" aria-live="polite" hidden></span>
    </div>
  </header>
  <main tabindex="0" aria-label="Task list">
    <section class="stream" id="stream" aria-live="polite"></section>
  </main>
  <script>
    const DISPLAY_ADVANCE_SECONDS = 15;

    let queue = [];
    let visibleTasks = [];
    let lastUpdatedIso = null;
    let lastWarnings = [];
    let lastErrorMessage = "";
    let fetchedCount = 0;
    let fetchedProjectCount = 0;
    let fetchedPriorityCount = 0;
    let nextAdvanceAtMs = Date.now() + DISPLAY_ADVANCE_SECONDS * 1000;
    let fetching = false;
    let advancing = false;
    let taskMutationVersion = 0;
    const completingTaskIds = new Set();

    const streamEl = document.getElementById("stream");
    const statusEl = document.getElementById("status");
    const actionStatusEl = document.getElementById("action-status");
    const projectCountEl = document.getElementById("project-count");
    const taskCountEl = document.getElementById("task-count");
    const priorityCountEl = document.getElementById("priority-count");
    const queueCountEl = document.getElementById("queue-count");
    const clockEl = document.getElementById("clock");
    const mainEl = document.querySelector("main");

    function taskKey(task) {
      return task.id || `${task.project}|${task.title}|${task.due || ""}`;
    }

    function uniqueTasks(tasks) {
      const seen = new Set();
      const unique = [];
      for (const task of tasks) {
        const key = taskKey(task);
        if (seen.has(key)) continue;
        seen.add(key);
        unique.push(task);
      }
      return unique;
    }

    function toLocal(iso) {
      if (!iso) return "Unknown time";
      const date = new Date(iso);
      if (Number.isNaN(date.getTime())) return "Unknown time";
      return date.toLocaleString();
    }

    function formatDue(task) {
      if (!task.due) return "No due date";
      const value = String(task.due);
      if (/^\\d{4}-\\d{2}-\\d{2}$/.test(value)) {
        const date = new Date(`${value}T00:00:00`);
        if (!Number.isNaN(date.getTime())) {
          return date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
        }
      }
      const date = new Date(value);
      if (!Number.isNaN(date.getTime())) {
        return date.toLocaleString(undefined, {
          weekday: "short",
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        });
      }
      return value;
    }

    function isTaskOverdue(task, now = new Date()) {
      if (!task.due) return false;
      const value = String(task.due);
      const dateOnly = /^\\d{4}-\\d{2}-\\d{2}$/.test(value);
      const due = new Date(dateOnly ? `${value}T00:00:00` : value);
      if (Number.isNaN(due.getTime())) return false;
      const cutoff = dateOnly
        ? new Date(now.getFullYear(), now.getMonth(), now.getDate())
        : now;
      return due < cutoff;
    }

    function priorityLabel(priority) {
      const value = Number(priority || 1);
      if (value >= 4) return "P1";
      if (value === 3) return "P2";
      if (value === 2) return "P3";
      return "P4";
    }

    function computeVisibleCount() {
      const h = window.innerHeight;
      const w = window.innerWidth;
      if (w < 700) {
        return h < 760 ? 3 : 4;
      }
      if (h < 760) return 4;
      if (h < 900) return 5;
      return 6;
    }

    function syncPills() {
      const tasks = [...visibleTasks, ...queue];
      fetchedCount = tasks.length;
      fetchedProjectCount = new Set(tasks.map((task) => task.project || "").filter(Boolean)).size;
      fetchedPriorityCount = tasks.filter((task) => Number(task.priority || 1) >= 4 || isTaskOverdue(task)).length;
      taskCountEl.textContent = `${fetchedCount} tasks`;
      projectCountEl.textContent = `${fetchedProjectCount} projects`;
      priorityCountEl.textContent = `${fetchedPriorityCount} high priority`;
      queueCountEl.textContent = `queue ${queue.length}`;
    }

    function renderStatus() {
      const remain = Math.max(0, Math.ceil((nextAdvanceAtMs - Date.now()) / 1000));
      if (lastErrorMessage) {
        statusEl.textContent = `Update failed: ${lastErrorMessage} | retry in ${remain}s`;
        return;
      }
      if (lastWarnings.length && !fetchedCount) {
        statusEl.textContent = `Warning: ${lastWarnings[0]} | retry in ${remain}s`;
        return;
      }
      if (!lastUpdatedIso) {
        statusEl.textContent = `Waiting for first update... | next change ${remain}s`;
        return;
      }
      const updated = toLocal(lastUpdatedIso);
      const warnText = lastWarnings.length ? ` | WARN ${lastWarnings.length}` : "";
      const changeText = mainEl.scrollTop > 0
        ? "auto change paused while scrolling"
        : `next change ${remain}s`;
      statusEl.textContent = `Updated ${updated}${warnText} | ${changeText}`;
    }

    function renderStream(highlightKey = "") {
      const visible = [...visibleTasks, ...queue];
      const scrollTop = mainEl.scrollTop;

      streamEl.textContent = "";
      if (!visible.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "No matching Todoist tasks. Check the filter or TODOIST_API_TOKEN.";
        streamEl.appendChild(empty);
        return;
      }

      for (const task of visible) {
        const key = taskKey(task);
        const item = document.createElement("article");
        const priority = Number(task.priority || 1);
        const overdue = isTaskOverdue(task);
        item.className = `task priority-${priority}`;
        if (overdue) item.classList.add("overdue");
        if (highlightKey && key === highlightKey) item.classList.add("new");
        const completing = completingTaskIds.has(task.id);
        item.classList.toggle("completing", completing);
        item.setAttribute("aria-busy", String(completing));

        const link = document.createElement("a");
        link.className = "task-link";
        link.target = "_blank";
        link.rel = "noreferrer noopener";
        link.href = task.link || `todoist://task?id=${encodeURIComponent(task.id || "")}`;

        const completeControl = document.createElement("label");
        completeControl.className = "complete-control";
        completeControl.title = "チェックするとTodoistでタスクを完了にします。";

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.className = "complete-checkbox";
        checkbox.dataset.taskId = task.id || "";
        checkbox.setAttribute("aria-label", `タスクを完了: ${task.title || "Untitled task"}`);
        checkbox.checked = completing;
        checkbox.disabled = completing || !task.id;
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) completeTask(task, checkbox);
        });

        const completeCaption = document.createElement("span");
        completeCaption.textContent = completing ? "処理中..." : "完了";
        completeControl.append(checkbox, completeCaption);

        const bar = document.createElement("div");
        bar.className = "priority-bar";

        const project = document.createElement("div");
        project.className = "project";
        project.textContent = task.project || "Todoist";

        const priorityEl = document.createElement("div");
        priorityEl.className = "priority";
        priorityEl.textContent = priorityLabel(priority);

        const title = document.createElement("div");
        title.className = "task-title";
        title.textContent = task.title || "Untitled task";

        const description = document.createElement("div");
        description.className = "description";
        description.textContent = task.description || "No description";

        const meta = document.createElement("div");
        meta.className = "task-meta";

        const due = document.createElement("span");
        due.className = "due";
        const duePrefix = overdue ? "Overdue" : "Due";
        due.textContent = `${duePrefix} ${formatDue(task)}`;
        meta.appendChild(due);

        if (task.due_text) {
          const dueText = document.createElement("span");
          dueText.textContent = task.due_text;
          meta.appendChild(dueText);
        }

        if (task.deadline) {
          const deadline = document.createElement("span");
          deadline.textContent = `Deadline ${task.deadline}`;
          meta.appendChild(deadline);
        }

        for (const label of (task.labels || []).slice(0, 4)) {
          const tag = document.createElement("span");
          tag.className = "tag";
          tag.textContent = `@${label}`;
          meta.appendChild(tag);
        }

        item.appendChild(bar);
        link.append(project, priorityEl, title, description, meta);
        item.append(link, completeControl);
        streamEl.appendChild(item);
      }
      mainEl.scrollTop = scrollTop;
    }

    function showActionStatus(message, isError = false) {
      actionStatusEl.textContent = message;
      actionStatusEl.classList.toggle("error", isError);
      actionStatusEl.hidden = false;
    }

    async function completeTask(task, checkbox) {
      if (!task.id || completingTaskIds.has(task.id)) return;
      const index = [...streamEl.querySelectorAll(".complete-checkbox")].indexOf(checkbox);
      completingTaskIds.add(task.id);
      checkbox.disabled = true;
      checkbox.closest(".task").classList.add("completing");
      checkbox.closest(".task").setAttribute("aria-busy", "true");
      checkbox.nextElementSibling.textContent = "処理中...";
      showActionStatus(`完了にしています: ${task.title}`);
      let completed = false;
      try {
        const response = await fetch(`/api/tasks/${encodeURIComponent(task.id)}/complete`, {
          method: "POST",
          cache: "no-store",
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.error || `HTTP ${response.status}`);
        }
        taskMutationVersion += 1;
        const completedIds = new Set([task.id, ...(payload.completed_ids || [])]);
        visibleTasks = visibleTasks.filter((item) => !completedIds.has(item.id));
        queue = queue.filter((item) => !completedIds.has(item.id));
        while (visibleTasks.length < computeVisibleCount() && queue.length) {
          visibleTasks.push(queue.shift());
        }
        completed = true;
        showActionStatus(`完了しました: ${task.title}`);
      } catch (error) {
        showActionStatus(`完了にできませんでした: ${task.title}: ${error.message || "unknown error"}。もう一度お試しください。`, true);
      } finally {
        completingTaskIds.delete(task.id);
        const restoreFocus = document.activeElement === checkbox || document.activeElement === document.body;
        syncPills();
        renderStream();
        renderStatus();
        if (restoreFocus) {
          const checkboxes = [...streamEl.querySelectorAll(".complete-checkbox")];
          const next = completed
            ? checkboxes[Math.min(index, checkboxes.length - 1)]
            : checkboxes.find((item) => item.dataset.taskId === task.id);
          if (next) next.focus({ preventScroll: true });
        }
      }
    }

    async function fetchQueue() {
      if (fetching) return false;
      fetching = true;
      const mutationVersion = taskMutationVersion;
      try {
        const response = await fetch("/api/tasks", { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const payload = await response.json();
        // Ignore responses started before a successful completion.
        if (mutationVersion !== taskMutationVersion) return false;
        const fresh = uniqueTasks(payload.tasks || payload.entries || []);
        const freshByKey = new Map(fresh.map((task) => [taskKey(task), task]));
        const pool = uniqueTasks([...visibleTasks, ...queue]);
        const knownKeys = new Set(pool.map((task) => taskKey(task)));
        const newTasks = fresh.filter((task) => !knownKeys.has(taskKey(task)));
        const currentTasks = pool
          .filter((task) => freshByKey.has(taskKey(task)))
          .map((task) => freshByKey.get(taskKey(task)));
        const merged = uniqueTasks([...newTasks, ...currentTasks]);
        const maxPoolSize = Math.max(fresh.length, computeVisibleCount());
        const trimmed = merged.slice(0, maxPoolSize);
        const targetSize = computeVisibleCount();
        visibleTasks = trimmed.slice(0, targetSize);
        queue = trimmed.slice(targetSize);
        lastUpdatedIso = payload.updated_at || null;
        lastWarnings = payload.errors || [];
        lastErrorMessage = "";
        syncPills();
        return true;
      } catch (error) {
        lastErrorMessage = error.message || "unknown error";
        return false;
      } finally {
        fetching = false;
      }
    }

    function rotateVisibleFromTop() {
      const targetSize = computeVisibleCount();
      while (visibleTasks.length < targetSize && queue.length) {
        visibleTasks.push(queue.shift());
      }

      if (!visibleTasks.length) return "";
      const removed = visibleTasks.shift();
      if (removed) {
        queue.push(removed);
      }

      while (visibleTasks.length < targetSize && queue.length) {
        visibleTasks.push(queue.shift());
      }

      const highlighted = visibleTasks[visibleTasks.length - 1];
      syncPills();
      return highlighted ? taskKey(highlighted) : "";
    }

    async function advanceStream() {
      if (advancing || completingTaskIds.size) return;
      advancing = true;
      try {
        const ok = await fetchQueue();
        if (!ok && !visibleTasks.length && !queue.length) {
          nextAdvanceAtMs = Date.now() + DISPLAY_ADVANCE_SECONDS * 1000;
          renderStatus();
          return;
        }

        const highlightKey = mainEl.scrollTop > 0 ? "" : rotateVisibleFromTop();
        renderStream(highlightKey);
        nextAdvanceAtMs = Date.now() + DISPLAY_ADVANCE_SECONDS * 1000;
        renderStatus();
      } finally {
        advancing = false;
      }
    }

    function tickClock() {
      const now = new Date();
      clockEl.textContent = now.toLocaleTimeString();
      const tasks = [...visibleTasks, ...queue];
      streamEl.querySelectorAll(".task").forEach((item, index) => {
        const task = tasks[index];
        const overdue = isTaskOverdue(task, now);
        if (item.classList.contains("overdue") === overdue) return;
        item.classList.toggle("overdue", overdue);
        item.querySelector(".due").textContent = `${overdue ? "Overdue" : "Due"} ${formatDue(task)}`;
      });
      const priorityCount = tasks
        .filter((task) => Number(task.priority || 1) >= 4 || isTaskOverdue(task, now)).length;
      if (priorityCount !== fetchedPriorityCount) {
        fetchedPriorityCount = priorityCount;
        syncPills();
      }
    }

    let resizeTimer = null;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        const target = computeVisibleCount();
        while (visibleTasks.length > target) {
          const overflow = visibleTasks.pop();
          if (overflow) {
            queue.unshift(overflow);
          }
        }
        while (visibleTasks.length < target && queue.length) {
          visibleTasks.push(queue.shift());
        }
        syncPills();
        renderStream();
      }, 220);
    });

    (async () => {
      tickClock();
      renderStatus();
      await fetchQueue();

      syncPills();
      renderStream();
      nextAdvanceAtMs = Date.now() + DISPLAY_ADVANCE_SECONDS * 1000;
      renderStatus();

      setInterval(advanceStream, DISPLAY_ADVANCE_SECONDS * 1000);
      setInterval(() => {
        tickClock();
        renderStatus();
      }, 1000);
    })();
  </script>
</body>
</html>
"""


class TodoistState:
    def __init__(self, progress=None) -> None:
        self.tasks: List[TodoistTask] = []
        self.errors: List[str] = []
        self.updated_at: Optional[str] = None
        self.lock = threading.Lock()
        self.complete_lock = threading.Lock()
        self.mutation_version = 0
        self.progress = progress
        self.activity_errors = []
        self.user_id = None
        self.refresh_callback = None
        self.refresh_lock = threading.Lock()
        self.filter_query = "next 7 days | overdue"

    def dashboard(self):
        with self.lock:
            payload = {"updated_at": self.updated_at, "tasks": [asdict(t) for t in self.tasks],
                       "errors": list(self.errors), "activity_errors": list(self.activity_errors),
                       "filter_query": self.filter_query}
        payload["progress"] = self.progress.snapshot() if self.progress else None
        return payload


def sync_activity(state, token, timeout, verify_ssl):
    if not state.progress or not token:
        return
    # Serialize with local completions so a successful close is recorded before
    # any matching activity events arrive. Both paths use the same XP ledger.
    with state.complete_lock:
        try:
            if not state.user_id:
                state.user_id = current_user_id(token, timeout, verify_ssl)
            until = now_iso()
            events = collect_activity(token, state.user_id, state.progress.sync_since(), until, timeout, verify_ssl)
            state.progress.ingest_activity(events, until)
            errors = []
        except (TodoistError, ValueError, sqlite3.Error, OSError) as exc:
            errors = [f"Todoist 完了履歴の同期を再試行します: {exc}"]
        with state.lock:
            state.activity_errors = errors


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_env_file(path: str) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


TOKEN_ENV_FALLBACKS = ("TODOIST_API_TOKEN", "TODOIST_API", "TODOIST_TOKEN")


def resolve_token(primary_env: str) -> Tuple[str, str]:
    """Return (token, source env var), trying primary_env before common fallback names."""
    candidates = [primary_env, *(name for name in TOKEN_ENV_FALLBACKS if name != primary_env)]
    for name in candidates:
        value = os.environ.get(name, "").strip()
        if value:
            return value, name
    return "", primary_env


def refresh_once(
    state: TodoistState,
    token: str,
    timeout: float,
    limit: int,
    filter_query: Optional[str],
    filter_lang: Optional[str],
    project_id: Optional[str],
    section_id: Optional[str],
    label: Optional[str],
    verify_ssl: bool,
) -> None:
    with state.lock:
        mutation_version = state.mutation_version
    tasks, errors = collect_tasks(
        token=token,
        timeout=timeout,
        limit=limit,
        filter_query=filter_query,
        filter_lang=filter_lang,
        project_id=project_id,
        section_id=section_id,
        label=label,
        verify_ssl=verify_ssl,
    )
    with state.lock:
        if mutation_version == state.mutation_version:
            if tasks or not errors:
                state.tasks = tasks
                state.updated_at = now_iso()
            state.errors = errors
    sync_activity(state, token, timeout, verify_ssl)


def refresh_loop(
    state: TodoistState,
    token: str,
    timeout: float,
    limit: int,
    filter_query: Optional[str],
    filter_lang: Optional[str],
    project_id: Optional[str],
    section_id: Optional[str],
    label: Optional[str],
    verify_ssl: bool,
    interval: float,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        if stop_event.wait(interval):
            break
        if not state.refresh_lock.acquire(blocking=False):
            continue
        try:
            refresh_once(
                state, token=token, timeout=timeout, limit=limit,
                filter_query=filter_query, filter_lang=filter_lang,
                project_id=project_id, section_id=section_id, label=label,
                verify_ssl=verify_ssl,
            )
        finally:
            state.refresh_lock.release()


def make_handler(
    state: TodoistState,
    token: str = "",
    timeout: float = 15.0,
    verify_ssl: bool = True,
):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, payload: object, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _html(self, payload: str) -> None:
            body = payload.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", maxsplit=1)[0]
            if path == "/":
                self._html((STATIC_DIR / "index.html").read_text(encoding="utf-8"))
                return

            if path == "/stream":
                self._html(HTML)
                return

            if path in ("/dashboard.js", "/dashboard.css"):
                body = (STATIC_DIR / path[1:]).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)
                return

            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return

            if path == "/api/dashboard":
                self._json(state.dashboard())
                return

            if path in ("/api/tasks", "/api/news"):
                with state.lock:
                    tasks = [asdict(item) for item in state.tasks]
                    payload = {
                        "updated_at": state.updated_at,
                        "tasks": tasks,
                        "entries": tasks,
                        "errors": list(state.errors),
                    }
                self._json(payload)
                return

            if path == "/healthz":
                with state.lock:
                    payload = {
                        "ok": not state.errors and not state.activity_errors,
                        "updated_at": state.updated_at,
                        "task_count": len(state.tasks),
                        "error_count": len(state.errors) + len(state.activity_errors),
                        "quests_enabled": state.progress is not None,
                    }
                self._json(payload)
                return

            self._json({"error": "not found"}, status=404)

        def do_DELETE(self) -> None:  # noqa: N802
            # Old open tabs must not be able to delete tasks after the update.
            self._json({"error": "Task deletion is disabled. Reload this page to complete tasks."}, status=405)

        def do_POST(self) -> None:  # noqa: N802
            origin = self.headers.get("Origin")
            if (
                self.headers.get("Sec-Fetch-Site") == "cross-site"
                or (origin and urlsplit(origin).netloc != self.headers.get("Host"))
            ):
                self._json({"error": "Cross-origin completion is not allowed."}, status=403)
                return

            path = self.path.split("?", maxsplit=1)[0]
            if path == "/api/refresh":
                if state.refresh_callback and state.refresh_lock.acquire(blocking=False):
                    try:
                        state.refresh_callback()
                    finally:
                        state.refresh_lock.release()
                self._json(state.dashboard())
                return
            quest_match = re.fullmatch(r"/api/quests/([a-z]+)", path)
            if quest_match:
                if not state.progress:
                    self._json({"error": "クエスト記録が利用できません。"}, status=503)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4096:
                        raise ValueError("記録データのサイズが正しくありません。")
                    if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                        raise ValueError("JSON 形式で記録してください。")
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError("記録の形式が正しくありません。")
                    if "amount" in body and body["amount"] is None:
                        raise ValueError("実績を数値で入力してください。")
                    progress = state.progress.save_quest(quest_match.group(1), body.get("day"), body.get("status"),
                                                        body.get("safe_bonus"), body.get("hold"),
                                                        amount=body.get("amount"), rest=body.get("rest"),
                                                        expected_amount=body.get("expected_amount"))
                except QuestConflict as exc:
                    self._json({"error": str(exc)}, status=409)
                    return
                except (ValueError, UnicodeDecodeError) as exc:
                    self._json({"error": str(exc)}, status=400)
                    return
                except sqlite3.Error:
                    self._json({"error": "記録を保存できませんでした。もう一度お試しください。"}, status=500)
                    return
                self._json({"progress": progress})
                return
            match = re.fullmatch(r"/api/tasks/([^/]+)/complete", path)
            if not match:
                self._json({"error": "not found"}, status=404)
                return
            task_id = unquote(match.group(1))
            if not token.strip():
                self._json({"error": "TODOIST_API_TOKEN is not set."}, status=503)
                return

            with state.complete_lock:
                with state.lock:
                    selected = next((task for task in state.tasks if task.id == task_id), None)
                if not selected:
                    self._json({"error": "Task is no longer in the list."}, status=404)
                    return
                try:
                    complete_task(token, task_id, timeout=timeout, verify_ssl=verify_ssl)
                except TodoistError as exc:
                    self._json({"error": str(exc)}, status=502)
                    return

                with state.lock:
                    completed_ids = {task_id}
                    # Recurring parents advance rather than permanently closing children.
                    while not selected.is_recurring:
                        children = {
                            task.id for task in state.tasks
                            if task.parent_id in completed_ids and task.id not in completed_ids
                        }
                        if not children:
                            break
                        completed_ids.update(children)
                    state.tasks = [task for task in state.tasks if task.id not in completed_ids]
                    state.mutation_version += 1
                    state.updated_at = now_iso()
                    payload = {"completed_ids": sorted(completed_ids), "updated_at": state.updated_at}
                if state.progress:
                    try:
                        state.progress.record_local_completion(task_id, selected.title)
                        payload["progress"] = state.progress.snapshot()
                    except sqlite3.Error:
                        # The task already closed upstream. Never invite another close;
                        # the next activity sync can recover its XP from Todoist.
                        payload["warning"] = "タスクは完了しました。経験値は完了履歴の次回同期で反映します。"
                self._json(payload)

        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            return

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Todoist signage server with auto-refresh.")
    parser.add_argument("--bind", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port")
    parser.add_argument("--limit", type=int, default=240, help="Max number of tasks to keep")
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout (seconds)")
    parser.add_argument("--refresh-seconds", type=float, default=60.0, help="Todoist refresh interval")
    parser.add_argument("--insecure", action="store_true", help="Disable SSL certificate verification")
    parser.add_argument("--env-file", default=".env", help="Path to an optional local env file")
    parser.add_argument("--token-env", default="TODOIST_API_TOKEN", help="Environment variable containing the Todoist token (TODOIST_API / TODOIST_TOKEN also work as fallbacks)")
    parser.add_argument("--filter", default="next 7 days | overdue", help="Todoist filter query to display")
    parser.add_argument("--filter-lang", help="Language tag for the Todoist filter query")
    parser.add_argument("--all", action="store_true", help="Show all active tasks instead of using --filter")
    parser.add_argument("--project-id", help="Project ID filter used with --all")
    parser.add_argument("--section-id", help="Section ID filter used with --all")
    parser.add_argument("--label", help="Label filter used with --all")
    parser.add_argument("--data-file", default=str(Path(__file__).resolve().parent / "data" / "quests.sqlite3"), help="Persistent quest/XP database")
    args = parser.parse_args()

    load_env_file(args.env_file)
    token, token_env = resolve_token(args.token_env)
    filter_query = None if args.all else args.filter

    if token:
        print(f"[INFO] Reading Todoist token from ${token_env}.")
    else:
        fallbacks = ", ".join(f"${name}" for name in TOKEN_ENV_FALLBACKS if name != args.token_env)
        print(f"[WARN] ${args.token_env} is not set (also checked {fallbacks}). The page will show setup guidance.")

    filter_label = "all active tasks" if filter_query is None else filter_query
    print(f"[INFO] Todoist task source: {filter_label}")

    state = TodoistState(QuestStore(args.data_file))
    state.filter_query = filter_query
    refresh_options = dict(
        token=token,
        timeout=args.timeout,
        limit=args.limit,
        filter_query=filter_query,
        filter_lang=args.filter_lang,
        project_id=args.project_id,
        section_id=args.section_id,
        label=args.label,
        verify_ssl=not args.insecure,
    )
    state.refresh_callback = lambda: refresh_once(state, **refresh_options)
    state.refresh_callback()
    print(f"[INFO] Loaded {len(state.tasks)} Todoist tasks at startup.")
    for error in state.errors:
        print(f"[WARN] {error}")

    stop_event = threading.Event()
    thread = threading.Thread(
        target=refresh_loop,
        kwargs={
            "state": state,
            "token": token,
            "timeout": args.timeout,
            "limit": args.limit,
            "filter_query": filter_query,
            "filter_lang": args.filter_lang,
            "project_id": args.project_id,
            "section_id": args.section_id,
            "label": args.label,
            "verify_ssl": not args.insecure,
            "interval": max(args.refresh_seconds, 5.0),
            "stop_event": stop_event,
        },
        daemon=True,
    )
    thread.start()

    server = ThreadingHTTPServer(
        (args.bind, args.port),
        make_handler(state, token=token, timeout=args.timeout, verify_ssl=not args.insecure),
    )
    print(f"[INFO] Signage running on http://{args.bind}:{args.port}")
    print("[INFO] Open / in a browser to display the Todoist task screen.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Stopping server...")
    finally:
        stop_event.set()
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
