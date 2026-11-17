"""Checks of building configurations from the schema the plugin carries: connections, workflows and solutions.

Standard library only, like the server: python3 -m unittest discover -s server -v
"""

import copy
import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_connection  # noqa: E402
import build_rules  # noqa: E402
import build_solution  # noqa: E402
import build_workflow  # noqa: E402
import schema_files  # noqa: E402
import yaml_out  # noqa: E402

SCHEMA = schema_files.Schema()

DESCRIPTION = {
    "connector": "orders",
    "label": "Order system",
    "description": "The shop's order system",
    "configuration": ["orders.base-url", "orders.api-key"],
    "operations": [
        {"name": "order.get", "kind": "read", "label": "Read an order", "arguments": {"id": "the order's id"},
         "result": {"id": None, "status": None, "total": None, "email": None}},
        {"name": "order.cancel", "kind": "command", "label": "Cancel an order", "arguments": {"id": "the order's id"},
         "result": {"id": None, "status": None}, "redo": "sets",
         "verify": {"operation": "order.get", "arguments": {"id": "${result.id}"}, "expect": "${verified.status == 'CANCELLED'}"}},
    ],
}


def route(operation, steps):
    return {"route": {"id": "orders.%s" % operation, "from": {"uri": "direct:orders.%s" % operation, "steps": steps}}}


ROUTES = [
    route("order.get", [
        {"setHeader": {"id": "orders.get.key", "name": "Authorization", "simple": "Bearer {{orders.api-key}}"}},
        {"toD": {"id": "orders.get.call", "uri": "{{orders.base-url}}/orders/${body[id]}"}},
        {"setBody": {"id": "orders.get.shape", "jq": "{id: .id, status: .state, total: .amount, email: .customer.email}"}},
    ]),
    route("order.cancel", [
        {"setHeader": {"id": "orders.cancel.key", "name": "Authorization", "simple": "Bearer {{orders.api-key}}"}},
        {"setHeader": {"id": "orders.cancel.method", "name": "CamelHttpMethod", "constant": "POST"}},
        {"toD": {"id": "orders.cancel.call", "uri": "{{orders.base-url}}/orders/${body[id]}/cancel"}},
    ]),
]


class Yaml(unittest.TestCase):
    def test_words_yaml_would_misread_are_quoted(self):
        written = yaml_out.dump({"outcomes": ["YES", "NO", "on", "007", "x: y", "plain words"]})
        self.assertIn('"NO"', written)
        self.assertIn('"on"', written)
        self.assertIn('"007"', written)
        self.assertIn('"x: y"', written)
        self.assertIn("- plain words", written)

    def test_text_of_several_lines_is_a_block(self):
        self.assertIn("instructions: |\n  one\n  two", yaml_out.dump({"instructions": "one\ntwo\n"}))


class Connection(unittest.TestCase):
    def test_a_well_made_connection_is_ready(self):
        report = build_connection.check(SCHEMA, DESCRIPTION, ROUTES)
        self.assertEqual([], report.problems, report.problems)
        files = build_connection.files(DESCRIPTION, ROUTES)
        self.assertEqual({"connector.yaml", "routes/orders.camel.yaml"}, set(files))

    def problems(self, description, routes):
        return " / ".join(p["what"] for p in build_connection.check(SCHEMA, description, routes).problems)

    def test_an_operation_without_a_route(self):
        self.assertIn("no route carries it out", self.problems(DESCRIPTION, ROUTES[:1]))

    def test_a_setting_the_description_does_not_list(self):
        routes = copy.deepcopy(ROUTES)
        routes[0]["route"]["from"]["steps"][0]["setHeader"]["simple"] = "Bearer {{orders.token}}"
        self.assertIn("orders.token", self.problems(DESCRIPTION, routes))

    def test_a_step_camel_does_not_have(self):
        routes = copy.deepcopy(ROUTES)
        routes[0]["route"]["from"]["steps"].append({"setHeaderz": {"name": "x", "constant": "y"}})
        self.assertIn("setHeaderz", json.dumps(build_connection.check(SCHEMA, DESCRIPTION, routes).problems))

    def test_an_option_the_component_does_not_have(self):
        routes = copy.deepcopy(ROUTES)
        routes[0]["route"]["from"]["steps"].append({"to": {"id": "x", "uri": "sql:select 1", "parameters": {"outputTyp": "SelectOne"}}})
        self.assertIn("not an option of sql", self.problems(DESCRIPTION, routes))

    def test_code_in_a_route(self):
        routes = copy.deepcopy(ROUTES)
        routes[0]["route"]["from"]["steps"].append({"setBody": {"id": "x", "groovy": "body.toString()"}})
        self.assertIn("carry no code", self.problems(DESCRIPTION, routes))

    def test_a_check_that_is_not_a_read(self):
        description = dict(DESCRIPTION, check={"operation": "order.cancel"})
        self.assertIn("not one of its reads", self.problems(description, ROUTES))

    def test_a_refusal_the_platform_does_not_know(self):
        routes = copy.deepcopy(ROUTES)
        routes[0]["route"]["from"]["steps"].append({"throwException": {"id": "x", "exceptionType": "java.lang.RuntimeException", "message": "no"}})
        self.assertIn("refuses with one of", self.problems(DESCRIPTION, routes))


PLAN = {
    "key": "orderThanks", "name": "Thank a customer for a paid order", "start": "read",
    "steps": [
        {"id": "read", "name": "Read the order", "block": "callConnector",
         "fields": {"connector": "orders", "operation": "order.get", "arg.id": "${subjectId}", "resultVariable": "order"},
         "next": "big", "onError": {"NOT_FOUND": "gone"}, "byHandMinutes": 1},
        {"id": "big", "name": "Is it a big order?", "choose": [{"when": "${order.total > 100}", "to": "ask"}, {"otherwise": True, "to": "write"}]},
        {"id": "ask", "name": "Ask what to add", "block": "runAgent", "fields": {"agent": "order-reader", "input.order": "${order}"}, "next": "write"},
        {"id": "write", "name": "Write the thanks", "block": "writeText", "fields": {"template": "order-thanks", "resultVariable": "letter"},
         "next": "approve", "byHandMinutes": 3},
        {"id": "approve", "name": "Send the thanks?", "approval": True, "next": "done", "timer": {"after": "P2D", "to": "late"}},
        {"id": "late", "name": "Nobody decided", "block": "escalate", "fields": {"reason": "Nobody decided whether to thank them"}, "next": "done"},
        {"id": "gone", "name": "The order is gone", "block": "conclude", "fields": {"outcome": "SKIPPED", "reason": "The order could not be read"}, "next": "done"},
        {"id": "done", "end": True},
    ],
}


class Workflow(unittest.TestCase):
    def test_a_well_made_plan_becomes_one_process(self):
        report = build_workflow.check(SCHEMA, PLAN)
        self.assertEqual([], report.problems, report.problems)
        xml = build_workflow.bpmn(SCHEMA, PLAN, "orders-care")
        root = ElementTree.fromstring(xml)
        processes = list(root.iter("{http://www.omg.org/spec/BPMN/20100524/MODEL}process"))
        self.assertEqual(["orderThanks"], [p.get("id") for p in processes])
        self.assertIn('flowable:async="true"', xml)
        self.assertIn('default="f', xml)

    def problems(self, plan, known=None):
        return " / ".join(p["what"] for p in build_workflow.check(SCHEMA, plan, known).problems)

    def test_a_value_used_before_it_is_worked_out_on_every_way(self):
        plan = copy.deepcopy(PLAN)
        plan["steps"][3]["fields"]["text"] = "{{workflow.decision.summary}}"
        self.assertIn("before it is worked out on every way", self.problems(plan))

    def test_an_error_the_step_never_ends_with(self):
        plan = copy.deepcopy(PLAN)
        plan["steps"][3]["onError"] = {"NOT_FOUND": "gone"}
        self.assertIn("never ends with NOT_FOUND", self.problems(plan))

    def test_a_required_field_left_out(self):
        plan = copy.deepcopy(PLAN)
        del plan["steps"][0]["fields"]["operation"]
        self.assertIn("needs operation", self.problems(plan))

    def test_a_step_nothing_leads_to(self):
        plan = copy.deepcopy(PLAN)
        plan["steps"].append({"id": "lost", "name": "Lost", "block": "escalate", "next": "done"})
        self.assertIn("Nothing leads to this step", self.problems(plan))

    def test_an_expression_the_engine_cannot_read(self):
        plan = copy.deepcopy(PLAN)
        plan["steps"][0]["fields"]["reason"] = "${'order ' += subjectId}"
        self.assertIn("texts are not joined with +=", self.problems(plan))
        plan["steps"][0]["fields"]["reason"] = "${subjectId == 'a' && subjectId != 'b'}"
        self.assertNotIn("cannot be read by the engine", self.problems(plan))

    def test_names_the_installation_does_not_have(self):
        known = {"connections": {"orders": {"order.get": "read"}}, "agents": set(), "texts": {"order-thanks"}}
        self.assertIn("There is no agent “order-reader”", self.problems(PLAN, known))


POLICY = {"key": "ordersPolicy", "name": "Orders: what may be done without asking", "about": "What goes out by itself", "policy": True,
          "inputs": [{"label": "Operation", "looksAt": "operation"}, {"label": "Amount", "looksAt": "order.total", "type": "number"}],
          "rows": [{"when": ["message.send", {"compare": "> 100"}], "then": ["NEEDS_APPROVAL"], "why": "Big orders are looked at"},
                   {"when": ["message.send", None], "then": ["AUTO"], "why": "Small thanks go by themselves"}]}


class Rules(unittest.TestCase):
    def test_a_policy_becomes_one_decision(self):
        plan = copy.deepcopy(POLICY)
        self.assertEqual([], build_rules.check(SCHEMA, plan).problems)
        root = ElementTree.fromstring(build_rules.dmn(plan))
        decisions = [e.get("id") for e in root.iter() if e.tag.endswith("}decision")]
        self.assertEqual(["ordersPolicy"], decisions)

    def problems(self, plan):
        return " / ".join(p["what"] for p in build_rules.check(SCHEMA, plan).problems)

    def test_several_values_in_one_cell(self):
        plan = copy.deepcopy(POLICY)
        plan["rows"][0]["when"][1] = {"compare": "\"a\",\"b\""}
        self.assertIn("give each value a row of its own", self.problems(plan))

    def test_a_verdict_a_policy_does_not_have(self):
        plan = copy.deepcopy(POLICY)
        plan["rows"][1]["then"] = ["SEND"]
        self.assertIn("NEEDS_APPROVAL", self.problems(plan))

    def test_a_row_with_too_few_cells(self):
        plan = copy.deepcopy(POLICY)
        plan["rows"][1]["when"] = ["message.send"]
        self.assertIn("1 cells under when, for 2 columns", self.problems(plan))


class AgentOrder(unittest.TestCase):
    def test_an_agent_comes_after_the_agents_it_names(self):
        agents = [{"key": "lead", "helpers": [{"agent": "reader"}, "finder"], "whenStuck": {"senior": "senior"}},
                  {"key": "checker", "helpers": [{"agent": "reader"}]},
                  {"key": "reader"}, {"key": "finder"}, {"key": "senior", "checker": "checker"}]
        order = [a["key"] for a in build_solution.agents_in_order(agents)]
        for before, after in (("reader", "lead"), ("finder", "lead"), ("senior", "lead"), ("reader", "checker"), ("checker", "senior")):
            self.assertLess(order.index(before), order.index(after), order)
        self.assertEqual(sorted(order), sorted(a["key"] for a in agents))

    def test_a_circle_keeps_every_agent_once(self):
        agents = [{"key": "a", "helpers": ["b"]}, {"key": "b", "helpers": ["a"]}]
        self.assertEqual(["b", "a"], [a["key"] for a in build_solution.agents_in_order(agents)])


class Solution(unittest.TestCase):
    def test_a_solution_and_its_files(self):
        with tempfile.TemporaryDirectory() as folder:
            os.makedirs(os.path.join(folder, "workflows"))
            os.makedirs(os.path.join(folder, "texts"))
            os.makedirs(os.path.join(folder, "rules"))
            with open(os.path.join(folder, "workflows", "orderThanks.bpmn20.xml"), "w") as f:
                f.write(build_workflow.bpmn(SCHEMA, PLAN, "orders-care"))
            with open(os.path.join(folder, "texts", "order-thanks.txt"), "w") as f:
                f.write("Thank you for your order.\n")
            with open(os.path.join(folder, "rules", "policy.dmn"), "w") as f:
                f.write('<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="d" name="d" namespace="x">'
                        '<decision id="ordersPolicy" name="Orders policy"/></definitions>')
            solution = {
                "solution": "orders-care", "decisions": ["rules/policy.dmn"],
                "agents": [{"key": "order-reader", "instructions": "Decide.", "tools": ["orders.order.get"], "outcomes": ["ADD", "NOTHING"]}],
                "caseTypes": [{"key": "order-thanks", "name": "Thank a customer", "process": "workflows/orderThanks.bpmn20.xml",
                               "processKey": "orderThanks", "startOn": ["order.paid"], "policy": "ordersPolicy", "shadow": True}],
                "texts": [{"key": "order-thanks", "name": "Thanks for an order", "file": "texts/order-thanks.txt"}],
            }
            known = {"connections": {"orders": {"order.get": "read", "order.cancel": "command"}}}
            report = build_solution.check(SCHEMA, folder, solution, known)
            self.assertEqual([], report.problems, report.problems)

            wrong = copy.deepcopy(solution)
            wrong["agents"][0]["tools"] = ["orders.order.cancel"]
            wrong["agents"][0]["outcomes"] = ["ADD", False]
            wrong["caseTypes"][0]["processKey"] = "orderThank"
            problems = json.dumps(build_solution.check(SCHEMA, folder, wrong, known).problems, ensure_ascii=False)
            self.assertIn("outcomes[1]", problems)


if __name__ == "__main__":
    unittest.main()
