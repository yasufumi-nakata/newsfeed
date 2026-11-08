"""Regression coverage for task completion without contacting a Todoist account."""

import io
import threading
import unittest
from http import HTTPStatus
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import rss_signage
import todoist_api


def task(task_id, parent_id=None):
    return todoist_api.TodoistTask(
        id=task_id, project="Test", title=f"Task {task_id}", description=None,
        link=f"https://app.todoist.com/app/task/{task_id}", due=None,
        due_text=None, deadline=None, priority=1, labels=[], added_at=None,
        updated_at=None, is_overdue=False, parent_id=parent_id,
    )


class TodoistCompleteTests(unittest.TestCase):
    @patch("todoist_api.urlopen")
    def test_empty_success_response_and_encoded_id(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = b""
        todoist_api.complete_task("test-token", "task/id", timeout=3, verify_ssl=False)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, f"{todoist_api.API_BASE}/tasks/task%2Fid/close")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 3)
        self.assertEqual(urlopen.call_args.kwargs["context"].verify_mode, 0)

    @patch("todoist_api.urlopen")
    def test_missing_task_is_not_reported_as_completed(self, urlopen):
        urlopen.side_effect = HTTPError("https://example.test", 404, "missing", {}, io.BytesIO())
        with self.assertRaisesRegex(todoist_api.TodoistError, "HTTP 404"):
            todoist_api.complete_task("test-token", "missing")

    @patch("todoist_api.urlopen")
    def test_authentication_and_network_failures_are_reported(self, urlopen):
        for error, expected in (
            (HTTPError("https://example.test", 401, "unauthorized", {}, io.BytesIO()), "authentication failed"),
            (HTTPError("https://example.test", 403, "forbidden", {}, io.BytesIO()), "rejected"),
            (URLError("offline"), "Could not reach Todoist"),
        ):
            with self.subTest(error=error):
                urlopen.side_effect = error
                with self.assertRaisesRegex(todoist_api.TodoistError, expected):
                    todoist_api.complete_task("test-token", "a")

    @patch("todoist_api.urlopen")
    def test_missing_credentials_or_id_do_not_send_requests(self, urlopen):
        for token, task_id in (("", "a"), ("test-token", "")):
            with self.assertRaises(todoist_api.TodoistError):
                todoist_api.complete_task(token, task_id)
        urlopen.assert_not_called()

    @patch("todoist_api.urlopen")
    def test_empty_get_response_is_still_an_error(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = b""
        with self.assertRaisesRegex(todoist_api.TodoistError, "invalid JSON"):
            todoist_api._request_json("test-token", "/tasks", {}, 3, True)


class SignageCompleteTests(unittest.TestCase):
    def setUp(self):
        self.state = rss_signage.TodoistState()
        # Deliberately put a grandchild before its parent to cover arbitrary order.
        self.state.tasks = [task("grandchild", "child"), task("root"), task("child", "root"), task("keep")]

    def complete(self, path="/api/tasks/root/complete", token="test-token", headers=None, method="POST"):
        handler = object.__new__(rss_signage.make_handler(self.state, token, timeout=3))
        handler.path = path
        handler.headers = headers or {"Host": "localhost:8080", "Origin": "http://localhost:8080"}
        handler._json = MagicMock()
        getattr(handler, f"do_{method}")()
        args, kwargs = handler._json.call_args
        return kwargs.get("status", HTTPStatus.OK), args[0]

    @patch("rss_signage.complete_task")
    def test_complete_removes_task_and_descendants_only(self, complete_task):
        status, payload = self.complete()
        self.assertEqual(status, 200)
        self.assertEqual(set(payload["completed_ids"]), {"root", "child", "grandchild"})
        self.assertEqual([item.id for item in self.state.tasks], ["keep"])
        self.assertEqual(self.state.mutation_version, 1)
        self.assertIsNotNone(self.state.updated_at)
        complete_task.assert_called_once_with("test-token", "root", timeout=3, verify_ssl=True)

    @patch("rss_signage.complete_task", side_effect=todoist_api.TodoistError("offline"))
    def test_failed_completion_preserves_tasks_for_retry(self, complete_task):
        original = list(self.state.tasks)
        status, payload = self.complete()
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"], "offline")
        self.assertEqual(self.state.tasks, original)
        self.assertEqual(self.state.mutation_version, 0)
        complete_task.side_effect = None
        self.assertEqual(self.complete()[0], 200)

    @patch("rss_signage.complete_task")
    def test_unknown_id_or_route_does_not_complete_anything(self, complete_task):
        for path in ("/api/tasks/missing/complete", "/api/tasks/", "/api/tasks/root/close", "/api/tasks/root", "/api/tasks"):
            with self.subTest(path=path):
                self.assertEqual(self.complete(path=path)[0], 404)
        complete_task.assert_not_called()

    @patch("rss_signage.complete_task")
    def test_missing_token_does_not_complete_anything(self, complete_task):
        self.assertEqual(self.complete(token="")[0], 503)
        complete_task.assert_not_called()

    @patch("rss_signage.complete_task")
    def test_cross_origin_requests_are_rejected(self, complete_task):
        for headers in (
            {"Host": "localhost:8080", "Origin": "https://example.test"},
            {"Host": "localhost:8080", "Sec-Fetch-Site": "cross-site"},
        ):
            with self.subTest(headers=headers):
                self.assertEqual(self.complete(headers=headers)[0], 403)
        complete_task.assert_not_called()

    @patch("rss_signage.complete_task")
    def test_inflight_refresh_cannot_restore_completed_tasks(self, complete_task):
        started = threading.Event()
        finish = threading.Event()
        old_tasks = list(self.state.tasks)

        def slow_collect(**kwargs):
            started.set()
            if not finish.wait(3):
                raise TimeoutError("Test did not release the refresh")
            return old_tasks, []

        with patch("rss_signage.collect_tasks", side_effect=slow_collect):
            thread = threading.Thread(target=rss_signage.refresh_once, kwargs={
                "state": self.state, "token": "test-token", "timeout": 3,
                "limit": 240, "filter_query": None, "filter_lang": None,
                "project_id": None, "section_id": None, "label": None,
                "verify_ssl": True,
            })
            thread.start()
            try:
                self.assertTrue(started.wait(3))
                self.assertEqual(self.complete()[0], 200)
            finally:
                finish.set()
                thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual([item.id for item in self.state.tasks], ["keep"])

    @patch("rss_signage.complete_task")
    def test_repeated_request_does_not_repeat_upstream_completion(self, complete_task):
        self.assertEqual(self.complete()[0], 200)
        self.assertEqual(self.complete()[0], 404)
        self.assertEqual(complete_task.call_count, 1)

    @patch("rss_signage.complete_task")
    def test_old_delete_endpoint_is_disabled(self, complete_task):
        original = list(self.state.tasks)
        status, payload = self.complete(path="/api/tasks/root", method="DELETE")
        self.assertEqual(status, 405)
        self.assertIn("Reload", payload["error"])
        self.assertEqual(self.state.tasks, original)
        complete_task.assert_not_called()

    @patch("rss_signage.complete_task")
    def test_next_occurrence_can_reappear_after_a_new_refresh(self, complete_task):
        self.assertEqual(self.complete()[0], 200)
        next_occurrence = task("root")
        next_occurrence.due = "2026-11-06"
        with patch("rss_signage.collect_tasks", return_value=([next_occurrence], [])):
            rss_signage.refresh_once(
                self.state, token="test-token", timeout=3, limit=240,
                filter_query=None, filter_lang=None, project_id=None,
                section_id=None, label=None, verify_ssl=True,
            )
        self.assertEqual(self.state.tasks, [next_occurrence])


if __name__ == "__main__":
    unittest.main()
