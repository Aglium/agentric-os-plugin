"""Builds a table of rules (a DMN decision table) from a plan and checks it the way the engine will read it.

A plan is data:
    {"key", "name", "about", "noRowFits", "policy": true when it judges a workflow's commands,
     "inputs": [{"label", "looksAt": "order.total", "type": "string|number|boolean", "about"}],
     "outputs": [{"name", "label", "type"}],            (a policy has one, verdict, and may have decides)
     "rows": [{"when": [cell, ...], "then": [value, ...] or {"output": value}, "why"}]}
A cell is a value it has to be ("text", 12, true), null for any value, or {"compare": "> 100"} for a comparison.
"""
import re
from xml.sax.saxutils import escape, quoteattr

from build_workflow import Report

KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
COMPARE = re.compile(r"^(<|<=|>|>=|!=|==)\s*(\"[^\"]*\"|-?[0-9]+(\.[0-9]+)?|true|false)$")


def check(schema, plan):
    report = Report()
    contract = schema.file("rules.json")
    if not isinstance(plan, dict):
        report.problem("plan", "has to be the table as an object: {key, name, inputs, outputs, rows}")
        return report
    if not isinstance(plan.get("key"), str) or not KEY.match(plan["key"]):
        report.problem("key", "The table needs a key of letters and digits starting with a letter, such as ordersCommandPolicy: workflows name it by it")
    if not plan.get("name"):
        report.problem("name", "The table needs a name people read, such as “Orders: what may be done without asking”")
    inputs, outputs, rows = plan.get("inputs") or [], plan.get("outputs") or [], plan.get("rows") or []
    if plan.get("policy"):
        allowed = contract["policy"]["output"]
        if not outputs:
            outputs = plan["outputs"] = [{"name": "verdict", "label": "Verdict", "type": "string"}]
        names = [o.get("name") for o in outputs]
        if names[0] != allowed["name"] or set(names) - {allowed["name"]} - set(contract["policy"]["outputsBesides"]):
            report.problem("outputs", "A policy answers %s first (and may say %s), nothing else" % (allowed["name"], ", ".join(contract["policy"]["outputsBesides"])))
    if not inputs:
        report.problem("inputs", "The table looks at nothing: say what each column looks at (looksAt), such as operation or order.total")
    if not outputs:
        report.problem("outputs", "The table answers nothing: say what each row answers (outputs: [{name, label}])")
    for i, column in enumerate(inputs):
        if not isinstance(column, dict) or not column.get("looksAt") or "${" in str(column.get("looksAt")):
            report.problem("inputs[%d]" % i, "says what it looks at as a name, without ${}: looksAt: order.total")
        if not (column or {}).get("label"):
            report.problem("inputs[%d]" % i, "needs a label people read")
    for i, output in enumerate(outputs):
        if not isinstance(output, dict) or not KEY.match(str(output.get("name") or "")):
            report.problem("outputs[%d]" % i, "needs a name of letters and digits, which a decide step keeps it under")
    if not rows:
        report.problem("rows", "The table has no rows")
    for r, row in enumerate(rows):
        where = "rows[%d]" % r
        if not isinstance(row, dict):
            report.problem(where, "has to be {when: [...], then: [...], why}")
            continue
        when = row.get("when") or []
        if len(when) != len(inputs):
            report.problem(where, "has %d cells under when, for %d columns" % (len(when), len(inputs)))
        for c, cell in enumerate(when):
            text = cell_text(cell)
            if text is None:
                report.problem("%s.when[%d]" % (where, c), "is not a cell: a value, null for any, or {compare: \"> 100\"}")
            elif isinstance(cell, dict) and not COMPARE.match(str(cell.get("compare", "")).strip()):
                report.problem("%s.when[%d]" % (where, c), "“%s” is not a comparison with one value, such as > 100 or != \"NEW\". %s"
                               % (cell.get("compare"), contract["cells"]["refused"]))
        then = row.get("then")
        values = [then.get(o.get("name")) for o in outputs] if isinstance(then, dict) else (then if isinstance(then, list) else [then])
        if len(values) != len(outputs):
            report.problem(where, "answers %d values for %d outputs" % (len(values), len(outputs)))
        if plan.get("policy") and values:
            verdicts = contract["policy"]["output"]["values"]
            if values[0] not in verdicts:
                report.problem(where, "answers %s; a policy answers one of: %s" % (values[0], ", ".join(verdicts)))
        if not row.get("why"):
            report.warn(where, "does not say why (why): people read the rules beside the workflow")
    if not plan.get("about"):
        report.warn("about", "Say what the rules are for, in words: it leads their page")
    return report


def cell_text(cell):
    if cell is None or cell == "" or cell == "-":
        return ""
    if isinstance(cell, bool):
        return "true" if cell else "false"
    if isinstance(cell, (int, float)):
        return str(cell)
    if isinstance(cell, str):
        return '"%s"' % cell.replace('"', "'")
    if isinstance(cell, dict) and "compare" in cell:
        return str(cell["compare"]).strip()
    return None


def dmn(plan, solution=None):
    key = plan["key"]
    outputs = plan["outputs"]
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="%s_definitions" name=%s namespace=%s>'
           % (key, quoteattr(plan["name"]), quoteattr("aglium.agentricos." + (solution or "rules"))),
           '  <decision id="%s" name=%s>' % (key, quoteattr(plan["name"]))]
    if plan.get("about"):
        out.append("    <description>%s</description>" % escape(plan["about"]))
    out.append('    <decisionTable id="%s_table" hitPolicy="FIRST">' % key)
    if plan.get("noRowFits") or plan.get("policy"):
        out.append("      <description>%s</description>" % escape(plan.get("noRowFits") or "A command no row mentions waits for a person."))
    for i, column in enumerate(plan["inputs"]):
        about = "<description>%s</description>" % escape(column["about"]) if column.get("about") else ""
        out.append('      <input id="%s_i%d" label=%s>%s<inputExpression id="%s_ie%d" typeRef="%s"><text>%s</text></inputExpression></input>'
                   % (key, i, quoteattr(column["label"]), about, key, i, column.get("type") or "string", escape(column["looksAt"])))
    for output in outputs:
        out.append('      <output id="%s_%s" label=%s name="%s" typeRef="%s"/>'
                   % (key, output["name"], quoteattr(output.get("label") or output["name"]), output["name"], output.get("type") or "string"))
    for r, row in enumerate(plan["rows"]):
        then = row.get("then")
        values = [then.get(o["name"]) for o in outputs] if isinstance(then, dict) else (then if isinstance(then, list) else [then])
        why = "<description>%s</description>" % escape(row["why"]) if row.get("why") else ""
        cells = "".join('<inputEntry id="%s_r%d_%d"><text>%s</text></inputEntry>' % (key, r, c, escape(cell_text(cell)))
                        for c, cell in enumerate(row["when"]))
        answers = "".join('<outputEntry id="%s_r%d_o%d"><text>%s</text></outputEntry>' % (key, r, o, escape(cell_text(value) if value is not None else ""))
                          for o, value in enumerate(values))
        out.append('      <rule id="%s_r%d">%s%s%s</rule>' % (key, r, why, cells, answers))
    out += ["    </decisionTable>", "  </decision>", "</definitions>"]
    return "\n".join(out) + "\n"
