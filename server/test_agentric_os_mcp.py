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
        self.assertIn("/plugin configure agentric-os@aglium", said)
        self.assertIn("config.json", said)

    def test_an_address_without_a_token_asks_for_the_token(self):
        os.environ["AGENTRIC_OS_URL"] = "https://env.example.com"
        said = self.refusal()
        self.assertIn("There is no access token", said)
        self.assertIn("AI agent access", said)
        self.assertIn("/plugin configure agentric-os@aglium", said)

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

    def test_get_exam_without_a_draft_or_an_exam_says_what_it_needs(self):
        answer = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_exam", "arguments": {}}})
        text = json.dumps(answer)
        self.assertIn("Say which draft", text)
        self.assertNotIn("Traceback", text)


class Versions(unittest.TestCase):
    def test_the_server_and_the_plugin_say_the_same_version(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, ".claude-plugin", "plugin.json"), encoding="utf-8") as file:
            self.assertEqual(json.load(file)["version"], server.VERSION)


if __name__ == "__main__":
    unittest.main()
