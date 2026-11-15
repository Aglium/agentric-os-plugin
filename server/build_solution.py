"""Checks a solution (solution.yaml and the files it names, in one folder) the way the installation will install it,
and writes solution.yaml from it.

The solution is given as data shaped like solution.yaml; its workflows, rules and texts are files of the folder
(workflows written by write_workflow). Everything it names has to be there: a file, the process of the key it says,
a table of rules its policy names, an agent, a text or a connection's operation a workflow or an agent uses.
"""
import os
import re
import xml.etree.ElementTree as ElementTree

import schema_check
import yaml_out
from build_workflow import Report

BPMN = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
FLOWABLE = "{http://flowable.org/bpmn}"
DMN = re.compile(r"\{https://www\.omg\.org/spec/DMN/[^}]*\}")


def decisions_in(path):
    """The keys (ids) of the decisions a DMN file defines."""
    root = ElementTree.parse(path).getroot()
    return [element.get("id") for element in root.iter() if element.tag.endswith("}decision") and element.get("id")]


def process_of(path):
    """The process a BPMN file holds: its id, and what its steps name ({field: [values]} of the blocks' fields)."""
    root = ElementTree.parse(path).getroot()
    processes = [p for p in root.iter(BPMN + "process")]
    named = {}
    for field in root.iter(FLOWABLE + "field"):
        texts = [t.text or "" for t in field if t.tag in (FLOWABLE + "string",)]
        if texts:
            named.setdefault(field.get("name"), []).append(texts[0].strip())
    return [p.get("id") for p in processes], named


def check(schema, folder, solution, known=None):
    """What is wrong with a solution in a folder. known: what the installation offers ({"connections": {name:
    {operation: kind}}, "agents": set, "texts": set, "rules": set, "calculations": set}, each left out when unknown)."""
    report = Report()
    known = known or {}
    folder = os.path.abspath(os.path.expanduser(folder))
    if not isinstance(solution, dict):
        report.problem("solution", "has to be the solution as an object, shaped like solution.yaml")
        return report
    for where, what in schema_check.problems(schema.file("solution.schema.json"), solution):
        report.problem(where or "solution", what)
    if report.problems:
        return report

    def exists(name, where):
        path = os.path.join(folder, *str(name).split("/"))
        if not os.path.isfile(path):
            report.problem(where, "names %s, which is not in %s" % (name, folder))
            return None
        return path

    rules = set(known.get("rules") or set())
    for i, name in enumerate(solution.get("decisions") or []):
        path = exists(name, "decisions[%d]" % i)
        if path:
            try:
                found = decisions_in(path)
            except ElementTree.ParseError as error:
                report.problem("decisions[%d]" % i, "%s is not a readable DMN document: %s" % (name, error))
                continue
            if not found:
                report.problem("decisions[%d]" % i, "%s defines no decision" % name)
            rules.update(found)
    texts = set(known.get("texts") or set()) | {t["key"] for t in solution.get("texts") or []}
    for i, text in enumerate(solution.get("texts") or []):
        exists(text["file"], "texts[%d]" % i)
    calculations = set(known.get("calculations") or set()) | {c["key"] for c in solution.get("calculations") or []}
    for i, calculation in enumerate(solution.get("calculations") or []):
        exists(calculation["file"], "calculations[%d]" % i)
    agents = set(known.get("agents") or set()) | {a["key"] for a in solution.get("agents") or []}
    connections = known.get("connections")

    for i, agent in enumerate(solution.get("agents") or []):
        where = "agents[%s]" % agent["key"]
        for list_name in ("tools", "commands"):
            for ref in agent.get(list_name) or []:
                connection, operation = ref.split(".", 1)
                if connections is None:
                    continue
                if connection not in connections:
                    report.problem(where, "%s names the connection “%s”, which is not there. The connections: %s"
                                   % (list_name, connection, ", ".join(sorted(connections)) or "none yet"))
                elif operation not in connections[connection]:
                    report.problem(where, "%s names “%s”, which “%s” does not offer" % (list_name, operation, connection))
                elif list_name == "tools" and connections[connection][operation] != "read":
                    report.problem(where, "tools are reads, but “%s” of “%s” changes something: list it under commands" % (operation, connection))
        for helper in agent.get("helpers") or []:
            other = helper if isinstance(helper, str) else helper.get("agent")
            if other not in agents:
                report.problem(where, "asks the agent “%s” for help, which is not there" % other)
        for other in (agent.get("checker"), (agent.get("whenStuck") or {}).get("senior")):
            if other and other not in agents:
                report.problem(where, "names the agent “%s”, which is not there" % other)

    keys = [c["key"] for c in solution.get("caseTypes") or []]
    for key in set(k for k in keys if keys.count(k) > 1):
        report.problem("caseTypes", "Two workflows have the key “%s”" % key)
    for case_type in solution.get("caseTypes") or []:
        where = "caseTypes[%s]" % case_type["key"]
        path = exists(case_type["process"], where)
        if case_type.get("policy") and case_type["policy"] not in rules:
            report.problem(where, "is judged by the rules “%s”, which no DMN file of the solution defines%s"
                           % (case_type["policy"], "" if known.get("rules") is None else " and the installation does not have"))
        if not case_type.get("shadow"):
            report.warn(where, "is not in Test run (shadow: true): a new workflow starts there, and goes Live as its own later change")
        if not case_type.get("startOn") and not case_type.get("schedule"):
            report.warn(where, "starts on no event and at no time: nothing will open a case of it")
        if not path:
            continue
        try:
            processes, named = process_of(path)
        except ElementTree.ParseError as error:
            report.problem(where, "%s is not a readable BPMN document: %s" % (case_type["process"], error))
            continue
        if processes != [case_type["processKey"]]:
            report.problem(where, "%s holds the process %s, not “%s” (processKey)" % (case_type["process"], ", ".join(processes) or "none", case_type["processKey"]))
        for agent in named.get("agent", []) + named.get("checker", []):
            if agent not in agents:
                report.problem(where, "a step asks the agent “%s”, which neither the solution nor the installation has" % agent)
        for text in named.get("template", []):
            if text not in texts:
                report.problem(where, "a step writes from the text “%s”, which neither the solution nor the installation has" % text)
        for said in named.get("rules", []):
            for table in said.split(","):
                if table.strip() not in rules:
                    report.problem(where, "a step asks the rules “%s”, which neither the solution nor the installation has" % table.strip())
        for calculation in named.get("calculation", []):
            if calculation not in calculations:
                report.problem(where, "a step works out the calculation “%s”, which neither the solution nor the installation has" % calculation)
        if connections is not None:
            for connection in named.get("connector", []):
                if connection not in connections:
                    report.problem(where, "a step calls the connection “%s”, which is not there" % connection)
    return report


def solution_yaml(solution):
    return "# What this solution consists of. Built with the agentric-os plugin.\n" + yaml_out.dump(solution)
