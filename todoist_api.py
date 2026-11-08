#!/usr/bin/env python3
"""Todoist API helpers for the local signage server."""

import json
import os
import ssl
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

API_BASE = "https://api.todoist.com/api/v1"
USER_AGENT = "ownnewsfeed-todoist-client/1.0"


class TodoistError(RuntimeError):
    """Raised when Todoist returns an unusable API response."""


_SYSTEM_CA_BUNDLES = ("/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem")


@lru_cache(maxsize=None)
def _build_ssl_context(verify_ssl: bool) -> ssl.SSLContext:
    if not verify_ssl:
        return ssl._create_unverified_context()

    context = ssl.create_default_context()
    if context.cert_store_stats().get("x509_ca"):
        return context

    # python.org builds on macOS ship without CA certificates; borrow certifi's
    # bundle or the system LibreSSL bundle so HTTPS verification still works.
    try:
        import certifi
        bundles = (certifi.where(), *_SYSTEM_CA_BUNDLES)
    except ImportError:
        bundles = _SYSTEM_CA_BUNDLES
    for cafile in bundles:
        if not os.path.exists(cafile):
            continue
        try:
            context.load_verify_locations(cafile=cafile)
            return context
        except (ssl.SSLError, OSError):
            continue
    return context


@dataclass
class TodoistTask:
    id: str
    project: str
    title: str
    description: Optional[str]
    link: str
    due: Optional[str]
    due_text: Optional[str]
    deadline: Optional[str]
    priority: int
    labels: List[str]
    added_at: Optional[str]
    updated_at: Optional[str]
    is_overdue: bool
    parent_id: Optional[str] = None
    is_recurring: bool = False


def _request_json(
    token: str,
    path: str,
    params: Dict[str, object],
    timeout: float,
    verify_ssl: bool,
    method: str = "GET",
) -> Dict[str, Any]:
    query = urlencode({key: value for key, value in params.items() if value not in (None, "")})
    url = f"{API_BASE}{path}"
    if query:
        url = f"{url}?{query}"

    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method=method,
    )
    context = _build_ssl_context(verify_ssl)

    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            body = response.read()
    except HTTPError as exc:
        with exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
        if exc.code == 401:
            raise TodoistError("Todoist authentication failed. Check TODOIST_API_TOKEN.") from exc
        if exc.code == 403:
            raise TodoistError("Todoist rejected this token or scope.") from exc
        message = f"Todoist HTTP {exc.code}"
        if detail:
            message = f"{message}: {detail[:220]}"
        raise TodoistError(message) from exc
    except URLError as exc:
        raise TodoistError(f"Could not reach Todoist: {exc.reason}") from exc
    except OSError as exc:
        raise TodoistError(f"Could not reach Todoist: {exc}") from exc

    if method == "POST" and not body:
        return {}

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TodoistError("Todoist returned an invalid JSON response.") from exc

    if not isinstance(payload, dict):
        raise TodoistError("Todoist returned an unexpected response shape.")
    return payload


def complete_task(
    token: str,
    task_id: str,
    timeout: float = 15.0,
    verify_ssl: bool = True,
) -> None:
    """Complete a task, advancing recurring tasks to their next occurrence."""
    if not token.strip():
        raise TodoistError("TODOIST_API_TOKEN is not set.")
    if not task_id:
        raise TodoistError("A task ID is required.")
    _request_json(
        token, f"/tasks/{quote(task_id, safe='')}/close", {}, timeout, verify_ssl,
        method="POST",
    )


def _fetch_paginated(
    token: str,
    path: str,
    params: Dict[str, object],
    timeout: float,
    verify_ssl: bool,
    limit: int,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    page_size = min(max(limit, 1), 200)

    while len(records) < limit:
        page_params = dict(params)
        page_params["limit"] = min(page_size, limit - len(records))
        if cursor:
            page_params["cursor"] = cursor

        payload = _request_json(token, path, page_params, timeout, verify_ssl)
        page = payload.get("results")
        if not isinstance(page, list):
            raise TodoistError("Todoist response did not include a results list.")

        records.extend(item for item in page if isinstance(item, dict))
        cursor_value = payload.get("next_cursor")
        cursor = cursor_value if isinstance(cursor_value, str) and cursor_value else None
        if not cursor:
            break

    return records[:limit]


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _due_sort_datetime(task: Dict[str, Any]) -> datetime:
    due = task.get("due")
    if isinstance(due, dict):
        due_date = due.get("date")
        if isinstance(due_date, str):
            parsed_dt = _parse_iso(due_date)
            if parsed_dt:
                return parsed_dt
            try:
                parsed_date = date.fromisoformat(due_date[:10])
                return datetime.combine(parsed_date, time.min, tzinfo=timezone.utc)
            except ValueError:
                pass

    updated_at = task.get("updated_at")
    if isinstance(updated_at, str):
        parsed_updated = _parse_iso(updated_at)
        if parsed_updated:
            return parsed_updated

    added_at = task.get("added_at")
    if isinstance(added_at, str):
        parsed_added = _parse_iso(added_at)
        if parsed_added:
            return parsed_added

    return datetime.max.replace(tzinfo=timezone.utc)


def _is_overdue(task: Dict[str, Any]) -> bool:
    due = task.get("due")
    if not isinstance(due, dict):
        return False
    due_date = due.get("date")
    if not isinstance(due_date, str):
        return False
    parsed = _due_sort_datetime(task)
    if "T" in due_date:
        return parsed < datetime.now(timezone.utc)
    return parsed.date() < date.today()


def _task_link(task_id: str) -> str:
    return f"https://app.todoist.com/app/task/{task_id}"


def _normalize_task(task: Dict[str, Any], project_names: Dict[str, str]) -> TodoistTask:
    task_id = str(task.get("id") or "")
    due = task.get("due")
    due_date = due.get("date") if isinstance(due, dict) else None
    due_text = due.get("string") if isinstance(due, dict) else None
    deadline = task.get("deadline")
    deadline_date = deadline.get("date") if isinstance(deadline, dict) else None
    labels = task.get("labels")
    project_id = str(task.get("project_id") or "")
    priority = task.get("priority")

    return TodoistTask(
        id=task_id,
        project=project_names.get(project_id, project_id or "Todoist"),
        title=str(task.get("content") or "Untitled task"),
        description=str(task.get("description") or "") or None,
        link=_task_link(task_id),
        due=str(due_date) if due_date else None,
        due_text=str(due_text) if due_text else None,
        deadline=str(deadline_date) if deadline_date else None,
        priority=int(priority) if isinstance(priority, int) else 1,
        labels=[str(label) for label in labels] if isinstance(labels, list) else [],
        added_at=str(task.get("added_at") or "") or None,
        updated_at=str(task.get("updated_at") or "") or None,
        is_overdue=_is_overdue(task),
        parent_id=str(task.get("parent_id") or "") or None,
        is_recurring=bool(due.get("is_recurring")) if isinstance(due, dict) else False,
    )


def _fetch_project_names(
    token: str,
    timeout: float,
    verify_ssl: bool,
    limit: int,
) -> Tuple[Dict[str, str], List[str]]:
    try:
        projects = _fetch_paginated(
            token=token,
            path="/projects",
            params={},
            timeout=timeout,
            verify_ssl=verify_ssl,
            limit=limit,
        )
    except TodoistError as exc:
        return {}, [f"Could not load Todoist project names: {exc}"]

    names: Dict[str, str] = {}
    for project in projects:
        project_id = project.get("id")
        name = project.get("name")
        if project_id and name:
            names[str(project_id)] = str(name)
    return names, []


def collect_tasks(
    token: str,
    timeout: float = 15.0,
    limit: int = 240,
    filter_query: Optional[str] = "next 7 days | overdue",
    filter_lang: Optional[str] = None,
    project_id: Optional[str] = None,
    section_id: Optional[str] = None,
    label: Optional[str] = None,
    verify_ssl: bool = True,
) -> Tuple[List[TodoistTask], List[str]]:
    """Fetch active Todoist tasks and return display-ready task records."""
    if not token.strip():
        return [], ["TODOIST_API_TOKEN is not set."]

    errors: List[str] = []
    project_names, project_errors = _fetch_project_names(token, timeout, verify_ssl, 200)
    errors.extend(project_errors)

    params: Dict[str, object] = {}
    path = "/tasks"
    if filter_query:
        path = "/tasks/filter"
        params["query"] = filter_query
        params["lang"] = filter_lang
    else:
        params["project_id"] = project_id
        params["section_id"] = section_id
        params["label"] = label

    try:
        raw_tasks = _fetch_paginated(
            token=token,
            path=path,
            params=params,
            timeout=timeout,
            verify_ssl=verify_ssl,
            limit=max(limit, 0),
        )
    except TodoistError as exc:
        return [], errors + [str(exc)]

    raw_tasks.sort(key=_due_sort_datetime)
    tasks = [_normalize_task(task, project_names) for task in raw_tasks]
    return tasks[: max(limit, 0)], errors


def current_user_id(token: str, timeout: float = 15.0, verify_ssl: bool = True) -> str:
    # /user may also include a token. Only retain the non-secret ID.
    user = _request_json(token, "/user", {}, timeout, verify_ssl)
    if not user.get("id"):
        raise TodoistError("Todoist user ID was not returned.")
    return str(user["id"])


def collect_activity(token: str, user_id: str, since: str, until: str,
                     timeout: float = 15.0, verify_ssl: bool = True) -> List[Dict[str, Any]]:
    """Get own completion/reopen events, including recurring task occurrences.

    Unlike completed-task snapshots, the activity log retains recurring events.
    Consume every page before the caller advances its persistent sync watermark.
    """
    # Own actions may carry either our user ID or a null initiator. Fetch both
    # representations, then exclude collaborators locally; filtering the request
    # by user ID alone can silently omit the null-initiator events.
    params = {"object_event_types": json.dumps(["item:completed", "item:uncompleted"]),
              "date_from": since, "date_to": until, "limit": 200}
    events, cursors = [], set()
    while True:
        payload = _request_json(token, "/activities", params, timeout, verify_ssl)
        page = payload.get("results")
        if not isinstance(page, list) or any(not isinstance(event, dict) for event in page):
            raise TodoistError("Todoist activity response did not include valid results.")
        # Some API responses represent one's own initiator as null.
        events.extend(event for event in page if event.get("initiator_id") is None or str(event["initiator_id"]) == user_id)
        cursor = payload.get("next_cursor")
        if not cursor:
            return events
        if not isinstance(cursor, str) or cursor in cursors:
            raise TodoistError("Todoist activity pagination returned an invalid cursor.")
        cursors.add(cursor)
        params["cursor"] = cursor
