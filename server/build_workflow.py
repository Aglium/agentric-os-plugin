"""Builds a workflow (a BPMN file of the platform) from a plan made of the blocks the schema describes, and checks it the
way the installation will, and further: a value used before any step worked it out, a way that leads nowhere, an
error caught that the step never ends with.

A plan is data: the workflow's key and name, the step it starts at, and its steps. A step is one of
    {"id", "name", "block": <a block of blocks.json>, "fields": {...}, "next", "onError": {CODE: step}, "timer": ...}
    {"id", "name", "choose": [{"when": "${...}", "to": step}, ..., {"otherwise": true, "to": step}]}
    {"id", "name", "set": {"variable": "${expression}"}, "next"}
    {"id", "name", "approval": true, "next", "timer": ...}         a person decides; the case gets `approved`
    {"id", "name", "end": true}
and may say what it is worth: "byHandMinutes", "byHandRole", "brings". A timer is {"after": "PT24H" | "at": "${...}",
"to": step}: the step is left that way once the time comes. The BPMN is written from the plan, never by hand.
"""
import re
from xml.sax.saxutils import escape, quoteattr

ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
EXPRESSION = re.compile(r"\$\{(.*?)\}", re.S)
PLACE = re.compile(r"\{\{\s*workflow\.([A-Za-z_][A-Za-z0-9_]*)")
WORD = re.compile(r"(?<![\w.'\"])([A-Za-z_][A-Za-z0-9_]*)")
STRINGS = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
JUEL_WORDS = {"true", "false", "null", "empty", "not", "and", "or", "eq", "ne", "lt", "gt", "le", "ge", "div", "mod",
              "instanceof", "execution"}
HELPERS = {"time", "history", "memory", "files", "settings", "dates", "values", "connections", "options", "connector"}
DEFAULT = re.compile(r"Default:\s*([A-Za-z_][A-Za-z0-9_]*)")
ESTIMATE = ("byHandMinutes", "byHandRole", "brings")


class Report:
    def __init__(self):
        self.problems, self.warnings = [], []

    def problem(self, where, what):
        self.problems.append({"where": where, "what": what})

    def warn(self, where, what):
        self.warnings.append({"where": where, "what": what})

    def answer(self, **more):
        result = {"ready": not self.problems, "problems": self.problems, "worthKnowing": self.warnings}
        result.update(more)
        return result


def check(schema, plan, known=None):
    """What is wrong with a plan. known: what the installation or the solution offers, to check names against:
    {"connections": {name: {operation: kind}}, "agents": set, "texts": set, "rules": set, "calculations": set}
    (each left out when not known)."""
    report = Report()
    known = known or {}
    blocks = {b["key"]: b for b in schema.file("blocks.json")}
    workflow = schema.file("workflow.json")
    if not isinstance(plan, dict):
        report.problem("plan", "has to be the plan as an object: {key, name, start, steps}")
        return report
    key = plan.get("key")
    if not isinstance(key, str) or not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", key):
        report.problem("key", "The workflow needs a key of letters and digits starting with a letter, such as orderPaid: it is the process's id")
    if not plan.get("name"):
        report.problem("name", "The workflow needs a name people read, such as “Thank a customer for an order”")
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        report.problem("steps", "The workflow has no steps")
        return report
    by_id = {}
    for i, step in enumerate(steps):
        where = "steps[%d]" % i
        if not isinstance(step, dict) or not isinstance(step.get("id"), str) or not ID.match(step["id"]):
            report.problem(where, "Each step needs an id of letters and digits, such as readOrder")
            continue
        if step["id"] in by_id or step["id"] == "start":
            report.problem(where, "The id “%s” is used twice (or is start, which the workflow's beginning has)" % step["id"])
        by_id[step["id"]] = step
    if plan.get("start") not in by_id:
        report.problem("start", "Say at which step the workflow starts: start is the id of one of its steps")
    for step in by_id.values():
        _check_step(step, by_id, blocks, known, report)
    if report.problems:
        return report
    _check_ways(plan, by_id, report)
    if report.problems:
        return report
    _check_values(plan, by_id, blocks, workflow, report)
    if not any(step.get("byHandMinutes") for step in by_id.values()) and not plan.get("byHandMinutes"):
        report.warn("plan", "Nothing says how long a person would take to do this work (byHandMinutes, byHandRole), so "
                            "the installation counts no saving for it. Say it on the steps that do a person's work, honestly")
    return report


def _kind_of(step):
    for kind in ("block", "choose", "set", "approval", "end"):
        if kind in step and step[kind] not in (None, False):
            return kind
    return None


def _check_step(step, by_id, blocks, known, report):
    where = "steps[%s]" % step["id"]
    kind = _kind_of(step)
    if kind is None:
        report.problem(where, "says neither block, choose, set, approval nor end: it is not known what it does")
        return
    if kind != "end" and not step.get("name"):
        report.problem(where, "needs a name people read, such as “Read the order”")
    targets = []
    if kind in ("block", "set", "approval"):
        targets.append(("next", step.get("next")))
    for code, target in (step.get("onError") or {}).items():
        targets.append(("onError." + code, target))
    if isinstance(step.get("timer"), dict):
        targets.append(("timer.to", step["timer"].get("to")))
    if kind == "choose":
        ways = step["choose"]
        if not isinstance(ways, list) or len(ways) < 2:
            report.problem(where, "A choice needs at least two ways: [{when, to}, ..., {otherwise: true, to}]")
            ways = []
        otherwise = [w for w in ways if isinstance(w, dict) and w.get("otherwise")]
        if len(otherwise) > 1:
            report.problem(where, "has more than one otherwise way")
        if not otherwise:
            report.warn(where, "has no otherwise way: a case none of the ways fit stops here with an error")
        for i, way in enumerate(ways):
            if not isinstance(way, dict):
                continue
            if not way.get("otherwise") and not (isinstance(way.get("when"), str) and way["when"].strip().startswith("${")):
                report.problem("%s.choose[%d]" % (where, i), "needs when: a condition written ${...}, or otherwise: true")
            targets.append(("choose[%d].to" % i, way.get("to")))
    for name, target in targets:
        if target not in by_id:
            report.problem("%s.%s" % (where, name), "leads to “%s”, which is not a step of this workflow" % target)
    if kind == "block":
        block = blocks.get(step["block"])
        if block is None:
            report.problem(where, "“%s” is not a block. The blocks are: %s" % (step["block"], ", ".join(blocks)))
            return
        if block.get("attachment") == "category":
            report.problem(where, "is an approval: write it as approval: true")
            return
        fields = step.get("fields") or {}
        described = {f["name"]: f for f in block["fields"]}
        groups = {f["name"] for f in block["fields"] if f["kind"] == "group"}
        for name in fields:
            if name not in described and name.split(".", 1)[0] not in groups:
                report.problem("%s.fields.%s" % (where, name), "is not a field of “%s”. Its fields: %s" % (block["name"], ", ".join(described)))
        for f in block["fields"]:
            if f.get("required") and f["kind"] != "group" and fields.get(f["name"]) in (None, ""):
                report.problem(where, "“%s” needs %s (%s): %s" % (block["name"], f["name"], f["label"], f["description"]))
        for code in step.get("onError") or {}:
            if code != "*" and code not in (block.get("errors") or []):
                report.problem("%s.onError.%s" % (where, code), "“%s” never ends with %s. It can end with: %s"
                               % (block["name"], code, ", ".join(block.get("errors") or []) or "nothing that can be caught"))
        if step.get("timer") is not None and block.get("element") != "bpmn:ReceiveTask":
            report.warn(where, "has a timer: the step is cut short when the time comes")
        _check_names(step, block, fields, known, report)
    if kind == "set":
        if not isinstance(step["set"], dict) or not step["set"]:
            report.problem(where, "set: says which values to work out, {name: \"${...}\"}")
        else:
            for name, value in step["set"].items():
                if not ID.match(str(name)) or not (isinstance(value, str) and value.strip().startswith("${") and value.strip().endswith("}")):
                    report.problem("%s.set.%s" % (where, name), "is worked out by one expression written ${...}")
    if step.get("timer") is not None:
        timer = step["timer"]
        if not isinstance(timer, dict) or bool(timer.get("after")) == bool(timer.get("at")):
            report.problem(where + ".timer", "says after (a duration such as PT24H, P3D) or at (a moment, ${...}), one of them")
    if kind not in ("block", "approval") and step.get("timer") is not None:
        report.problem(where + ".timer", "Only a step that does something or waits can have a timer")


def _check_names(step, block, fields, known, report):
    where = "steps[%s]" % step["id"]
    connections = known.get("connections")
    for f in block["fields"]:
        value = fields.get(f["name"])
        if not isinstance(value, str) or "${" in value:
            continue
        if f["kind"] == "connector" and connections is not None and value not in connections:
            report.problem(where, "There is no connection “%s”. The connections: %s" % (value, ", ".join(sorted(connections)) or "none yet"))
        if f["kind"] == "operation" and connections is not None:
            offered = connections.get(fields.get("connector"))
            if offered is not None and value not in offered:
                report.problem(where, "The connection “%s” offers no “%s”. It offers: %s" % (fields.get("connector"), value, ", ".join(sorted(offered))))
        if f["kind"] == "agent" and known.get("agents") is not None and value not in known["agents"]:
            report.problem(where, "There is no agent “%s”. The agents: %s" % (value, ", ".join(sorted(known["agents"])) or "none yet"))
        if f["name"] == "template" and known.get("texts") is not None and value not in known["texts"]:
            report.problem(where, "There is no text “%s”. The texts: %s" % (value, ", ".join(sorted(known["texts"])) or "none yet"))
        if f["name"] == "rules" and known.get("rules") is not None:
            for rules in value.split(","):
                if rules.strip() not in known["rules"]:
                    report.problem(where, "There are no rules “%s”. The tables of rules: %s" % (rules.strip(), ", ".join(sorted(known["rules"])) or "none yet"))
        if f["kind"] == "calculation" and known.get("calculations") is not None and value not in known["calculations"]:
            report.problem(where, "There is no calculation “%s”. The calculations: %s" % (value, ", ".join(sorted(known["calculations"])) or "none yet"))


def _after(step):
    """The steps a step may lead to."""
    kind = _kind_of(step)
    nexts = []
    if kind in ("block", "set", "approval") and step.get("next"):
        nexts.append(step["next"])
    if kind == "choose":
        nexts += [w.get("to") for w in step["choose"] if isinstance(w, dict)]
    nexts += list((step.get("onError") or {}).values())
    if isinstance(step.get("timer"), dict):
        nexts.append(step["timer"].get("to"))
    return [n for n in nexts if n]


def _check_ways(plan, by_id, report):
    seen, todo = set(), [plan["start"]]
    while todo:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        todo += _after(by_id[current])
    for step_id in by_id:
        if step_id not in seen:
            report.problem("steps[%s]" % step_id, "Nothing leads to this step")
    ends = {s for s, step in by_id.items() if _kind_of(step) == "end"}
    if not ends:
        report.problem("steps", "The workflow has no end: add a step {id, end: true}")
        return
    for step_id, step in by_id.items():
        if _kind_of(step) in ("block", "set", "approval") and not step.get("next"):
            report.problem("steps[%s]" % step_id, "says nothing about what comes next (next)")
    # every step can still reach an end
    reaches = set(ends)
    changed = True
    while changed:
        changed = False
        for step_id, step in by_id.items():
            if step_id not in reaches and any(n in reaches for n in _after(step)):
                reaches.add(step_id)
                changed = True
    for step_id in by_id:
        if step_id not in reaches:
            report.problem("steps[%s]" % step_id, "From here the workflow never comes to an end")


def _sets(step, blocks):
    """The variables a step leaves set once it is done."""
    kind = _kind_of(step)
    if kind == "set":
        return set(step["set"])
    if kind == "approval":
        return {"approved"}
    if kind != "block":
        return set()
    block = blocks[step["block"]]
    fields = step.get("fields") or {}
    for f in block["fields"]:
        if f["name"] == "resultVariable":
            if fields.get("resultVariable"):
                return {fields["resultVariable"]}
            default = DEFAULT.search(f.get("description") or "")
            return {default.group(1)} if default else set()
    return set()


def _used(value):
    """The variables a field's value names: in ${...} and as {{workflow.x}} places of a text."""
    names = set()
    if not isinstance(value, str):
        return names
    for expression in EXPRESSION.findall(value):
        bare = STRINGS.sub("''", expression)
        for word in WORD.findall(bare):
            names.add(word)
    names.update(PLACE.findall(value))
    return names


def _check_values(plan, by_id, blocks, workflow, report):
    known_at_start = set(workflow["variables"]["atStart"]) | set(workflow["variables"]["setByTheWorkflow"])
    # what is surely set when a step begins: what all the ways into it set, worked out until nothing changes
    every = set().union(*(_sets(s, blocks) for s in by_id.values())) | known_at_start
    into = {s: [] for s in by_id}
    for step_id, step in by_id.items():
        for n in _after(step):
            # a step left by an error or a timer did not finish: what it would have set is not set that way
            through_error = n in (step.get("onError") or {}).values() or (isinstance(step.get("timer"), dict) and step["timer"].get("to") == n)
            if not (_kind_of(step) == "block" and through_error and n != step.get("next")):
                into[n].append((step_id, True))
            else:
                into[n].append((step_id, False))
    surely = {s: set(every) for s in by_id}
    surely[plan["start"]] = set(known_at_start)
    changed = True
    while changed:
        changed = False
        for step_id in by_id:
            if step_id == plan["start"]:
                incoming = [set(known_at_start)]
            else:
                incoming = []
            for source, finished in into[step_id]:
                incoming.append(surely[source] | (_sets(by_id[source], blocks) if finished else set()))
            now = set.intersection(*incoming) if incoming else set(known_at_start)
            if now != surely[step_id]:
                surely[step_id] = now
                changed = True
    for step_id, step in by_id.items():
        values = []
        values += list((step.get("fields") or {}).values())
        values += list((step.get("set") or {}).values()) if isinstance(step.get("set"), dict) else []
        values += [w.get("when") for w in step.get("choose") or [] if isinstance(w, dict)]
        if isinstance(step.get("timer"), dict):
            values.append(step["timer"].get("at"))
        used = set()
        for value in values:
            used |= _used(value)
        for name in sorted(used - JUEL_WORDS - HELPERS):
            if name in surely[step_id]:
                continue
            if name in every:
                report.problem("steps[%s]" % step_id, "uses “%s” before it is worked out on every way here: on some way to this step no step has set it yet" % name)
            else:
                report.problem("steps[%s]" % step_id, "uses “%s”, which no step of the workflow sets and a case does not start with" % name)


# --- writing ---------------------------------------------------------------------------------------------------

def bpmn(schema, plan, solution=None):
    """The workflow as BPMN XML, from a plan check() passed."""
    blocks = {b["key"]: b for b in schema.file("blocks.json")}
    namespaces = schema.file("workflow.json")["document"]["namespaces"]
    steps = {s["id"]: s for s in plan["steps"]}
    errors = sorted({code for s in plan["steps"] for code in (s.get("onError") or {}) if code != "*"})
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<definitions xmlns=%s xmlns:flowable=%s xmlns:agentric=%s targetNamespace=%s>'
           % (quoteattr(namespaces["bpmn"]), quoteattr(namespaces["flowable"]), quoteattr(namespaces["agentric"]),
              quoteattr("aglium.agentricos." + (solution or "workflows")))]
    for code in errors:
        out.append('  <error id="error_%s" name="%s" errorCode="%s"/>' % (code.lower(), code, code))
    if plan.get("about"):
        out.append("  <!-- %s -->" % escape(plan["about"]).replace("--", "-"))
    out.append('  <process id=%s name=%s isExecutable="true"%s>' % (quoteattr(plan["key"]), quoteattr(plan["name"]), _worth(plan)))
    out.append('    <startEvent id="start"/>')
    flows = [("start", plan["start"], None, False)]
    for step in plan["steps"]:
        out += _element(step, blocks)
        kind = _kind_of(step)
        if kind in ("block", "set", "approval"):
            flows.append((step["id"], step["next"], None, False))
        if kind == "choose":
            for way in step["choose"]:
                flows.append((step["id"], way["to"], way.get("when"), bool(way.get("otherwise"))))
        for code, target in (step.get("onError") or {}).items():
            boundary = "%s_%s" % (step["id"], "error" if code == "*" else code.lower())
            ref = "" if code == "*" else ' errorRef="error_%s"' % code.lower()
            out.append('    <boundaryEvent id="%s" attachedToRef="%s"><errorEventDefinition%s/></boundaryEvent>' % (boundary, step["id"], ref))
            flows.append((boundary, target, None, False))
        if isinstance(step.get("timer"), dict):
            timer = step["timer"]
            boundary = step["id"] + "_time"
            definition = ("<timeDuration>%s</timeDuration>" % escape(timer["after"])) if timer.get("after") else ("<timeDate>%s</timeDate>" % escape(timer["at"]))
            out.append('    <boundaryEvent id="%s" attachedToRef="%s"><timerEventDefinition>%s</timerEventDefinition></boundaryEvent>' % (boundary, step["id"], definition))
            flows.append((boundary, timer["to"], None, False))
    for n, (source, target, condition, otherwise) in enumerate(flows, 1):
        flow_id = "f%d" % n
        if otherwise:
            out = [line.replace('<exclusiveGateway id="%s"' % source, '<exclusiveGateway id="%s" default="%s"' % (source, flow_id)) for line in out]
        if condition:
            out.append('    <sequenceFlow id="%s" sourceRef="%s" targetRef="%s"><conditionExpression><![CDATA[%s]]></conditionExpression></sequenceFlow>'
                       % (flow_id, source, target, _cdata(condition)))
        else:
            out.append('    <sequenceFlow id="%s" sourceRef="%s" targetRef="%s"/>' % (flow_id, source, target))
    out.append("  </process>")
    out.append("</definitions>")
    return "\n".join(out) + "\n"


def _worth(item):
    return "".join(" agentric:%s=%s" % (k, quoteattr(str(item[k]))) for k in ESTIMATE if item.get(k) not in (None, ""))


def _cdata(text):
    return str(text).replace("]]>", "]]]]><![CDATA[>")


def _field(name, value):
    value = "" if value is None else value
    if isinstance(value, (bool, int, float)):
        value = str(value).lower() if isinstance(value, bool) else str(value)
    if isinstance(value, (dict, list)):
        raise ValueError("field %s: a value is a text or an expression" % name)
    kind = "expression" if "${" in value else "string"
    return '<flowable:field name=%s><flowable:%s><![CDATA[%s]]></flowable:%s></flowable:field>' % (quoteattr(name), kind, _cdata(value), kind)


def _element(step, blocks):
    kind = _kind_of(step)
    sid, name = step["id"], quoteattr(step.get("name") or step["id"])
    if kind == "end":
        return ['    <endEvent id="%s"/>' % sid]
    if kind == "choose":
        return ['    <exclusiveGateway id="%s" name=%s/>' % (sid, name)]
    if kind == "approval":
        return ['    <userTask id="%s" name=%s flowable:category="approval"%s/>' % (sid, name, _worth(step))]
    if kind == "set":
        script = "".join("${execution.setVariable('%s', %s)}" % (k, v.strip()[2:-1].strip()) for k, v in step["set"].items())
        return ['    <scriptTask id="%s" name=%s scriptFormat="juel" flowable:autoStoreVariables="false"%s>' % (sid, name, _worth(step)),
                '      <script><![CDATA[%s]]></script>' % _cdata(script), '    </scriptTask>']
    block = blocks[step["block"]]
    fields = step.get("fields") or {}
    lines = []
    if block["attachment"] == "listener":
        lines.append('    <receiveTask id="%s" name=%s%s>' % (sid, name, _worth(step)))
        lines.append('      <extensionElements>')
        lines.append('        <flowable:executionListener event="start" delegateExpression="%s">' % block["expression"])
        lines += ["          " + _field(k, v) for k, v in fields.items()]
        lines.append('        </flowable:executionListener>')
        lines.append('        <flowable:executionListener event="end" delegateExpression="%s"/>' % block["expression"])
        lines.append('      </extensionElements>')
        lines.append('    </receiveTask>')
        return lines
    asynchronous = ' flowable:async="true"' if block.get("async") or step.get("async") else ""
    lines.append('    <serviceTask id="%s" name=%s%s%s flowable:delegateExpression="%s">' % (sid, name, asynchronous, _worth(step), block["expression"]))
    if fields:
        lines.append('      <extensionElements>')
        lines += ["        " + _field(k, v) for k, v in fields.items()]
        lines.append('      </extensionElements>')
    lines.append('    </serviceTask>')
    return lines
