"""Checks of where the server takes the installation's address and the access token from.

Standard library only, like the server: python3 -m unittest discover -s server -v
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agentric_os_mcp as server  # noqa: E402


class Settings(unittest.TestCase):
    """Each test has a home folder of its own and an environment that names nothing."""

    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.file = os.path.join(home.name, ".config", "agentric-os", "config.json")
        clean = {k: v for k, v in os.environ.items() if not k.startswith("AGENTRIC_OS_")}
        clean.update(HOME=home.name, USERPROFILE=home.name)
        environment = mock.patch.dict(os.environ, clean, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def keep(self, kept, mode=0o600):
        os.makedirs(os.path.dirname(self.file), exist_ok=True)
        with open(self.file, "w", encoding="utf-8") as file:
            file.write(kept if isinstance(kept, str) else json.dumps(kept))
        os.chmod(self.file, mode)

    def refusal(self):
        with self.assertRaises(server.Refused) as refused:
            server.where_and_token()
        return str(refused.exception)

    def test_the_file_is_used_when_the_environment_says_nothing(self):
        self.keep({"url": "https://file.example.com/", "token": "aos_from_file"})
        self.assertEqual(server.where_and_token(), ("https://file.example.com", "aos_from_file"))

    def test_the_environment_comes_before_the_file(self):
        self.keep({"url": "https://file.example.com", "token": "aos_from_file"})
        os.environ["AGENTRIC_OS_URL"] = "https://env.example.com/"
        os.environ["AGENTRIC_OS_TOKEN"] = "aos_from_env"
        self.assertEqual(server.where_and_token(), ("https://env.example.com", "aos_from_env"))

    def test_each_value_is_taken_on_its_own(self):
        self.keep({"url": "https://file.example.com", "token": "aos_from_file"})
        os.environ["AGENTRIC_OS_URL"] = "https://env.example.com"
        self.assertEqual(server.where_and_token(), ("https://env.example.com", "aos_from_file"))

    def test_a_placeholder_nobody_filled_in_counts_as_not_set(self):
        self.keep({"url": "https://file.example.com", "token": "aos_from_file"})
        os.environ["AGENTRIC_OS_URL"] = "${user_config.url}"
        os.environ["AGENTRIC_OS_TOKEN"] = "${user_config.token}"
        self.assertEqual(server.where_and_token(), ("https://file.example.com", "aos_from_file"))

    def test_the_file_is_not_needed_when_the_environment_has_both(self):
        self.keep({"url": "https://file.example.com", "token": "aos_from_file"}, mode=0o644)
        os.environ["AGENTRIC_OS_URL"] = "https://env.example.com"
        os.environ["AGENTRIC_OS_TOKEN"] = "aos_from_env"
        self.assertEqual(server.where_and_token(), ("https://env.example.com", "aos_from_env"))

    @unittest.skipIf(os.name == "nt", "file rights are not told this way on Windows")
    def test_a_file_only_its_owner_can_read_is_used(self):
        self.keep({"url": "https://file.example.com", "token": "aos_from_file"}, mode=0o600)
        self.assertEqual(server.where_and_token()[1], "aos_from_file")

    @unittest.skipIf(os.name == "nt", "file rights are not told this way on Windows")
    def test_a_file_others_can_read_is_refused_and_says_what_to_do(self):
        self.keep({"url": "https://file.example.com", "token": "aos_from_file"}, mode=0o644)
        said = self.refusal()
        self.assertIn("other people on this computer can read it", said)
        self.assertIn("chmod 600", said)
        self.assertNotIn("aos_from_file", said)

    def test_nothing_set_says_what_is_missing_and_where_to_enter_it(self):
        said = self.refusal()
        self.assertIn("It is not known where the installation is", said)
        self.assertIn("/plugin configure agentric-os@agentric-os", said)
        self.assertIn("config.json", said)

    def test_an_address_without_a_token_asks_for_the_token(self):
        os.environ["AGENTRIC_OS_URL"] = "https://env.example.com"
        said = self.refusal()
        self.assertIn("There is no access token", said)
        self.assertIn("AI agent access", said)
        self.assertIn("/plugin configure agentric-os@agentric-os", said)

    def test_a_file_that_is_not_understood_says_how_it_should_read(self):
        self.keep("not json")
        self.assertIn("is not understood", self.refusal())
        self.keep(["a", "list"])
        self.assertIn("is not understood", self.refusal())

    def test_a_refusal_never_reads_like_a_crash(self):
        said = self.refusal()
        for word in ("Traceback", "KeyError", "Errno", "None"):
            self.assertNotIn(word, said)

    def test_a_tool_called_before_anything_is_set_answers_in_words(self):
        answer = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": "overview", "arguments": {}}})
        text = json.dumps(answer)
        self.assertIn("It is not known where the installation is", text)
        self.assertNotIn("Traceback", text)


class Exams(unittest.TestCase):
    """An exam is read with what matters first, and a tool asked without what it needs says so in words."""

    def test_an_exam_of_a_draft_leads_with_better_or_worse_and_what_it_cost(self):
        said = server.exam({"status": "done", "name": "Refund writer", "done": 7, "examples": 7, "spent": 0.21, "limit": 2.0,
                            "verdict": "worse", "before": {"right": 3, "of": 7}, "after": {"right": 2, "of": 7}, "worse": []})
        self.assertEqual(list(said)[:6], ["exam", "name", "examplesDone", "spent", "limit", "result"])
        self.assertEqual(said["result"], "worse: right in 2 of 7, it was 3")
        self.assertEqual(said["exam"], "finished")

    def test_an_exam_still_running_says_when_to_ask_again(self):
        said = server.exam({"status": "running", "done": 2, "examples": 7, "after": {"right": 0, "of": 0}})
        self.assertIn("ask again", said["exam"])
        self.assertEqual(said["examplesDone"], "2 of 7")
        self.assertEqual(said["result"], "not known yet: no example is finished")

    def test_an_exam_asked_to_stop_says_it_is_stopping(self):
        said = server.exam({"status": "running", "stopping": True, "done": 1, "examples": 7, "after": {"right": 1, "of": 1}})
        self.assertTrue(said["exam"].startswith("stopping after the example it is on"))

    def test_get_exam_without_a_draft_or_an_exam_says_what_it_needs(self):
        answer = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_exam", "arguments": {}}})
        text = json.dumps(answer)
        self.assertIn("Say which draft", text)
        self.assertNotIn("Traceback", text)


class Problems(unittest.TestCase):
    """A draft names the problem it is about, and the problems, situations and Test run can be read."""

    def setUp(self):
        self.calls = []
        self.real = server.call
        server.call = lambda method, path, body=None, query=None: self.calls.append((method, path, body, query)) or {}

    def tearDown(self):
        server.call = self.real

    def run_tool(self, name, arguments):
        tool = next(t for t in server.TOOLS if t["name"] == name)
        return tool["run"](arguments)

    def test_a_draft_about_a_problem_says_which(self):
        self.run_tool("propose_change", {"kind": "agent", "target": "helper", "payload": {}, "note": "why", "problem": 7})
        self.assertEqual(self.calls[-1][:3], ("POST", "/api/changes", {"kind": "agent", "target": "helper", "payload": {}, "note": "why", "problem": 7}))

    def test_problems_situations_and_test_runs_are_read_where_the_installation_keeps_them(self):
        self.run_tool("list_problems", {"gone": True})
        self.assertEqual(self.calls[-1][1], "/api/problems")
        self.assertEqual(self.calls[-1][3]["gone"], "true")
        self.run_tool("get_problem", {"id": 3})
        self.assertEqual(self.calls[-1][1], "/api/problems/3")
        self.run_tool("list_repeating_situations", {})
        self.assertEqual(self.calls[-1][1], "/api/situations/repeating")
        self.run_tool("close_situation", {"key": "k", "way": "instruct", "text": "do so"})
        self.assertEqual(self.calls[-1][:3], ("POST", "/api/situations/close", {"key": "k", "way": "instruct", "text": "do so"}))
        self.run_tool("test_run", {"workflow": "answers"})
        self.assertEqual(self.calls[-1][1], "/api/case-types/answers/test-run")
        self.run_tool("test_run", {})
        self.assertEqual(self.calls[-1][1], "/api/test-runs/waiting")

    def test_an_approval_with_changed_values_says_why_and_repeated_changes_are_read(self):
        self.run_tool("decide_approval", {"commandId": "c1", "approved": True})
        self.assertEqual(self.calls[-1][:3], ("POST", "/api/approvals/c1", {"approved": True}))
        self.run_tool("decide_approval", {"commandId": "c1", "approved": True, "arguments": {"amount": 300}, "reason": "A discount"})
        self.assertEqual(self.calls[-1][:3], ("POST", "/api/approvals/c1", {"approved": True, "arguments": {"amount": 300}, "reason": "A discount"}))
        self.run_tool("list_repeated_changes", {"days": 7})
        self.assertEqual(self.calls[-1][1], "/api/approvals/repeated-changes")
        self.assertEqual(self.calls[-1][3]["days"], 7)

    def test_a_value_is_explained_and_the_weak_ones_listed_where_the_installation_keeps_them(self):
        self.run_tool("explain_value", {"caseId": "c1", "name": "decision.claimed.revenue"})
        self.assertEqual(self.calls[-1][:2], ("GET", "/api/cases/c1/values/explain"))
        self.assertEqual(self.calls[-1][3], {"name": "decision.claimed.revenue", "action": None})
        self.run_tool("explain_value", {"caseId": "c1", "name": "amount", "action": "a1"})
        self.assertEqual(self.calls[-1][3]["action"], "a1")
        self.run_tool("list_weak_values", {"caseId": "c1"})
        self.assertEqual(self.calls[-1][1], "/api/cases/c1/values")
        self.run_tool("get_kept_answer", {"caseId": "c1", "read": 42})
        self.assertEqual(self.calls[-1][1], "/api/cases/c1/reads/42")
        self.run_tool("get_case_decisions", {"caseId": "c1"})
        self.assertEqual(self.calls[-1][1], "/api/cases/c1/decisions")

    def test_questions_of_agents_are_read_and_answered_where_the_installation_keeps_them(self):
        self.run_tool("list_questions", {})
        self.assertEqual(self.calls[-1][1], "/api/questions")
        self.run_tool("answer_question", {"caseId": "c1", "questionId": "q1", "answer": "It was unplugged"})
        self.assertEqual(self.calls[-1][:3], ("POST", "/api/cases/c1/questions/q1", {"answer": "It was unplugged"}))

    def test_a_workflow_is_drafted_from_a_description_and_its_launch_read(self):
        self.run_tool("draft_workflow_from_description", {"description": "Record supplier invoices", "byHandMinutes": 12})
        self.assertEqual(self.calls[-1][:3], ("POST", "/api/workflow-launches", {"description": "Record supplier invoices", "byHandMinutes": 12}))
        self.run_tool("get_workflow_launch", {"id": "l1"})
        self.assertEqual(self.calls[-1][1], "/api/workflow-launches/l1")
        self.run_tool("get_workflow_launch", {"workflow": "invoices"})
        self.assertEqual(self.calls[-1][1], "/api/case-types/invoices/launch")
        self.run_tool("try_workflow_draft", {"id": "l1"})
        self.assertEqual(self.calls[-1][:2], ("POST", "/api/workflow-launches/l1/try"))
        self.run_tool("set_workflow_draft_senders", {"id": "l1", "senderDomains": ["@acme.test"]})
        self.assertEqual(self.calls[-1][:3], ("POST", "/api/workflow-launches/l1/routing", {"senderDomains": ["@acme.test"], "senders": []}))
        self.run_tool("set_workflow_draft_senders", {"id": "l1", "remove": True})
        self.assertEqual(self.calls[-1][:2], ("POST", "/api/workflow-launches/l1/routing/remove"))


class Versions(unittest.TestCase):
    def test_the_server_and_the_plugin_say_the_same_version(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, ".claude-plugin", "plugin.json"), encoding="utf-8") as file:
            self.assertEqual(json.load(file)["version"], server.VERSION)


if __name__ == "__main__":
    unittest.main()
