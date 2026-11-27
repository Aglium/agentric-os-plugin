#!/usr/bin/env python3
"""MCP server for an installed Agentric OS.

Lets a local AI agent (Claude Code, Cursor, Codex, ...) look at an installation and propose changes to it through
the installation's HTTP API, with an access token the installation issued (Control Center, "AI agent access").

Only the Python standard library is used, so nothing has to be installed besides Python 3.9+. It speaks MCP over
stdio: one JSON-RPC message per line.

Where the installation is and the access token are taken, each on its own, from the first place that has them:

    1. the environment: AGENTRIC_OS_URL (e.g. https://ops.example.com) and AGENTRIC_OS_TOKEN (aos_...). Claude Code
       fills these in from what it asked for and keeps (`/plugin configure agentric-os@agentric-os`).
    2. the file ~/.config/agentric-os/config.json: {"url": "https://ops.example.com", "token": "aos_..."}. It holds a
       secret, so it is read only when nobody but its owner can read it (chmod 600).

What the agent may do is decided by the installation, not here: a token that may only look cannot propose, and a
token without the right to apply cannot make a draft live. Every change is a draft first; nothing an agent
proposes runs until it is applied.
"""

import base64
import json
import mimetypes
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

VERSION = "0.2.0"
PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
TIMEOUT = float(os.environ.get("AGENTRIC_OS_TIMEOUT", "60"))


class Refused(Exception):
    """An answer the agent should read as what went wrong, not as a crash."""


# --- the installation's HTTP API -------------------------------------------------------------------------------

CONFIG_FILE = os.path.join("~", ".config", "agentric-os", "config.json")
HOW_TO_SET = ("In Claude Code type `/plugin configure agentric-os@agentric-os` and paste them when asked. For any other "
              "assistant put {\"url\": \"https://...\", \"token\": \"aos_...\"} into %s and run `chmod 600` on it."
              % CONFIG_FILE)


def from_environment(name):
    """A value of the environment; one whose placeholder nobody filled in ("${...}") counts as not there."""
    value = os.environ.get(name, "").strip()
    return "" if "${" in value else value


def from_file():
    """What the settings file says, or nothing when there is no file. Refuses a file others can read."""
    path = os.path.expanduser(CONFIG_FILE)
    try:
        with open(path, encoding="utf-8") as file:
            # the rights that matter are those of the file that was opened, not of a name that can change
            if os.name != "nt" and os.fstat(file.fileno()).st_mode & 0o077:
                raise Refused("The file %s holds the access token but other people on this computer can read it, "
                              "so it is not used. Make it yours alone: chmod 600 %s" % (CONFIG_FILE, CONFIG_FILE))
            kept = json.load(file)
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise Refused("The file %s cannot be read (%s)." % (CONFIG_FILE, error.strerror or error))
    except ValueError:
        raise Refused("The file %s is not understood. It should read: "
                      "{\"url\": \"https://ops.example.com\", \"token\": \"aos_...\"}" % CONFIG_FILE)
    if not isinstance(kept, dict):
        raise Refused("The file %s is not understood. It should read: "
                      "{\"url\": \"https://ops.example.com\", \"token\": \"aos_...\"}" % CONFIG_FILE)
    return kept


def where_and_token():
    """The installation's address and the access token: the environment first, then the settings file."""
    base = from_environment("AGENTRIC_OS_URL")
    token = from_environment("AGENTRIC_OS_TOKEN")
    if not base or not token:
        kept = from_file()
        base = base or str(kept.get("url") or "").strip()
        token = token or str(kept.get("token") or "").strip()
    if not base:
        raise Refused("It is not known where the installation is (its address, e.g. https://ops.example.com). "
                      + HOW_TO_SET)
    if not token:
        raise Refused("There is no access token. Create an access in the Control Center under 'AI agent access'. "
                      + HOW_TO_SET)
    return base.rstrip("/"), token


def call(method, path, body=None, query=None):
    return send(method, path, json.dumps(body).encode("utf-8") if body is not None else None, "application/json", query)


def local_file(local_path):
    """A file of this computer, read from disk as it is: (its name, its bytes)."""
    local_path = os.path.expanduser(str(local_path or "").strip())
    if not local_path:
        raise Refused("Say which file to give: its path on this computer.")
    if not os.path.isfile(local_path):
        raise Refused("There is no file at %s on this computer. Give the path of an existing file." % local_path)
    try:
        with open(local_path, "rb") as file:
            return os.path.basename(local_path), file.read()
    except OSError as error:
        raise Refused("The file %s cannot be read (%s)." % (local_path, error.strerror or error))


def send_file(path, local_path):
    """Sends a file of this computer to the installation as a form with one part, `file`, read from disk as it is."""
    name, content = local_file(local_path)
    kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
    boundary = "agentric-os-" + uuid.uuid4().hex
    head = ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\nContent-Type: %s\r\n\r\n'
            % (boundary, name.replace('"', "'"), kind)).encode("utf-8")
    data = head + content + ("\r\n--%s--\r\n" % boundary).encode("utf-8")
    return send("POST", path, data, "multipart/form-data; boundary=" + boundary)


def send(method, path, data, content_type, query=None):
    base, token = where_and_token()
    url = base + path
    if query:
        query = {k: v for k, v in query.items() if v is not None and v != ""}
        if query:
            url += "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", "agentric-os-mcp/" + VERSION)
    if data is not None:
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            text = response.read().decode("utf-8")
            if not text.strip():
                return {"done": True}
            try:
                return json.loads(text)
            except ValueError:
                return {"text": text}
    except urllib.error.HTTPError as error:
        raise Refused(explain(error.code, error.read().decode("utf-8", "replace"), method, path))
    except urllib.error.URLError as error:
        raise Refused("The installation at %s cannot be reached (%s). Check the address and the network."
                      % (base, error.reason))
    except (TimeoutError, socket.timeout):
        raise Refused("The installation did not answer within %d seconds." % TIMEOUT)


def explain(status, text, method, path):
    """The installation says in words why it refused; that is what the agent and its user should read."""
    detail = text.strip()
    try:
        parsed = json.loads(detail)
        if isinstance(parsed, dict):
            detail = parsed.get("detail") or parsed.get("message") or parsed.get("problems") or ""
            if not isinstance(detail, str):
                detail = json.dumps(detail, ensure_ascii=False)
    except ValueError:
        pass
    # what the web server answers when the installation itself said nothing, e.g. "404 NOT_FOUND"
    if detail.upper().replace(" ", "_") in ("%d_%s" % (status, word) for word in
                                            ("NOT_FOUND", "FORBIDDEN", "UNAUTHORIZED", "CONFLICT")):
        detail = ""
    detail = detail[:2000]
    if status == 401:
        return detail or ("The installation does not accept this token: it is wrong, expired or was revoked. "
                          "Ask an administrator of the installation for a new one.")
    if status == 403:
        return detail or ("This access is not allowed to do that (%s %s). What it may do is set on the access in "
                          "the Control Center; a person with more rights can do it there instead." % (method, path))
    if status == 404:
        return (detail or "Not found: %s" % path) + ". Check the name or key with the matching list tool."
    if status == 405:
        return "There is nothing to %s at %s." % ("read" if method == "GET" else "do", path)
    if status == 409:
        return "Refused because things changed meanwhile: %s" % detail
    if status == 422:
        return "Not accepted, what is wrong: %s" % detail
    if status >= 500:
        return "The installation failed to answer (%d): %s" % (status, detail)
    return "Refused (%d): %s" % (status, detail)


def quote(value):
    return urllib.parse.quote(str(value), safe="")


# --- tools -----------------------------------------------------------------------------------------------------

TOOLS = []


def tool(name, description, properties=None, required=None, read_only=True, destructive=False):
    def register(function):
        TOOLS.append({
            "name": name,
            "description": description,
            "inputSchema": {"type": "object", "properties": properties or {}, "required": required or [],
                            "additionalProperties": False},
            "annotations": {"readOnlyHint": read_only, "destructiveHint": destructive, "openWorldHint": False},
            "run": function,
        })
        return function
    return register


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}


@tool("overview",
      "Start here. What the installation is doing and what it is worth: cases waiting for a person, in progress and "
      "done today, and the money of the last `days` days (spent on models, saved in people's time, earned), all "
      "workflows together and each one.",
      {"days": dict(I, description="period for the money, default 30")})
def overview(a):
    return {"now": call("GET", "/api/overview"),
            "results": call("GET", "/api/results", query={"days": a.get("days", 30)})}


@tool("list_workflows", "Workflows (case types): what starts each, whether it runs in Test run or Live, whether it is "
      "on, its tags, and the versions per tenant.")
def list_workflows(a):
    return call("GET", "/api/case-types")


@tool("get_workflow",
      "One workflow with its process (BPMN XML) and its published versions. Read this before proposing a change to it.",
      {"key": S, "tenantId": S, "version": dict(I, description="an earlier published version")}, ["key"])
def get_workflow(a):
    return call("GET", "/api/case-types/%s/definition" % quote(a["key"]),
                query={"tenantId": a.get("tenantId"), "version": a.get("version")})


@tool("list_blocks",
      "What a workflow's process may be built from: each block with its BPMN element, fields and the errors it can "
      "end with. Read before writing or changing a process.")
def list_blocks(a):
    return call("GET", "/api/blocks")


@tool("check_workflow",
      "Check a process before proposing it: what is wrong and what is only a warning. Changes nothing.",
      {"processKey": S, "bpmnXml": S}, ["processKey", "bpmnXml"])
def check_workflow(a):
    return call("POST", "/api/case-types/validate", {"processKey": a["processKey"], "bpmnXml": a["bpmnXml"]})


@tool("list_rules", "Tables of rules (DMN decisions) workflows ask, including the policies that decide which actions "
      "need a person's approval.")
def list_rules(a):
    return call("GET", "/api/decisions")


@tool("get_rules", "One table of rules with its DMN XML.", {"key": S}, ["key"])
def get_rules(a):
    return call("GET", "/api/decisions/%s" % quote(a["key"]))


@tool("list_texts", "The shared library of texts workflows write from: each with the values it has places for and "
      "the workflows and texts using it.")
def list_texts(a):
    return call("GET", "/api/texts")


@tool("get_text", "One text of the library.", {"key": S}, ["key"])
def get_text(a):
    return call("GET", "/api/texts/%s" % quote(a["key"]))


@tool("list_calculations", "Calculations: numbers workflows work out by formulas, line by line, never by AI (a price after "
      "a discount, a commission, a valuation). Each with its lines (given by the step, or worked out by a formula over the "
      "lines above, said in words), the lines worked out with the examples, the workflows working it out, and how often it "
      "was worked out in the last 30 days and the work by hand that saved. One worked out by the business's own Excel "
      "model (byWorkbook) names a cell of the model for each line instead of a formula. A case's calculated lines are "
      "explained by explain_value (their name is the step's result and the line, such as model.total).")
def list_calculations(a):
    return call("GET", "/api/calculation-sheets")


@tool("get_calculation", "One calculation with its versions.", {"key": S}, ["key"])
def get_calculation(a):
    return call("GET", "/api/calculation-sheets/%s" % quote(a["key"]))


@tool("try_calculation", "Works a calculation out on values without changing anything: a kept one by its key, or the "
      "lines of one being written (as a change of kind calculation carries them). Answers every line with its value and "
      "its formula with the numbers put in, or what stopped it in words. A calculation with columns (such as years) "
      "answers a line by columns with each cell; a check line that does not come to zero is named with its column.",
      {"calculation": S, "lines": {"type": "array", "items": {"type": "object"}, "description": "lines of one being written, instead of a key"},
       "columns": {"type": "array", "items": {"type": "object"}, "description": "columns of one being written: [{key, name, group}]"},
       "values": {"type": "object", "description": "the given lines by key, as numbers: 0.15 for 15%; a line by columns "
                  "as a list in the order of the columns it is given in, or {column: number}"},
       "workbookPath": dict(S, description="the Excel model on this computer the lines being written name cells of; "
                                           "none: the model of `calculation`, if it has one")}, ["values"])
def try_calculation(a):
    body = {"values": a.get("values") or {}}
    if a.get("lines"):
        body["lines"] = a["lines"]
        if a.get("columns"):
            body["columns"] = a["columns"]
        if a.get("calculation"):
            body["calculation"] = a["calculation"]
        if a.get("workbookPath"):
            body["workbook"] = base64.b64encode(local_file(a["workbookPath"])[1]).decode("ascii")
    else:
        body["calculation"] = a.get("calculation")
    return call("POST", "/api/calculation-sheets/try", body)


@tool("read_calculation_workbook",
      "Read a business's own Excel model (an .xlsx on this computer) into the lines of a calculation worked out by it, "
      "instead of writing its formulas again: the installation proposes lines from the model's labels and formulas (each "
      "number formulas use is given, each formula nothing uses is a result), checked against the model and worked out with "
      "its numbers. No AI is asked there and nothing is paid. Answers {name, description, columns, lines (each with its "
      "cell), problems, warnings, note}; nothing is kept. Read the model yourself to give the lines the names the business "
      "uses, mark assumptions and checks, set columns for a model by periods, and keep the keys a calculation's workflows "
      "already give; then propose_change kind calculation with the lines as payload and workbookPath, the same file. "
      "Needs the right to propose changes.",
      {"path": dict(S, description="the model's path on this computer")}, ["path"], read_only=False)
def read_calculation_workbook(a):
    name, content = local_file(a["path"])
    return send("POST", "/api/calculation-sheets/read-workbook", content, "application/octet-stream",
                {"name": name})


@tool("try_calculation_on_past_cases", "Works the latest cases a calculation worked out (20 unless `limit` says) out "
      "again with the values each was given then, and the lines as kept or as given in `lines` (a change being written), "
      "and says for each what came out then and now, the change as a share, or why it could not be worked out again. "
      "Use it before proposing a change of a calculation, to say what it would do. Changes nothing.",
      {"calculation": S, "lines": {"type": "array", "items": {"type": "object"}}, "limit": I}, ["calculation"])
def try_calculation_on_past_cases(a):
    body = {"calculation": a["calculation"], "limit": a.get("limit", 20)}
    if a.get("lines"):
        body["lines"] = a["lines"]
    return call("POST", "/api/calculation-sheets/past", body)


@tool("list_agents", "AI agent profiles: instructions, model, the reads they may use, the actions they may propose.")
def list_agents(a):
    return call("GET", "/api/agents")


@tool("get_agent_claims",
      "How an agent's values with quotes went lately (an agent told to give its values with the exact words of their "
      "source, `claims` of its profile): how many values the platform could not confirm and why (words not found, "
      "words saying another value, a value given without words), how many cases went to a person because of it and "
      "what their time cost, what decisions with quotes cost on AI next to those without, and the value most often not "
      "confirmed. Switching it on or changing it is a `agent` change with `claims: {values, unconfirmed: mark|person}`.",
      {"agent": dict(S, description="the agent's key, as list_agents gives it"),
       "days": dict(I, description="how far back, 30 unless said")}, ["agent"])
def get_agent_claims(a):
    return call("GET", "/api/agents/%s/claims" % quote(a["agent"]), query={"days": a.get("days")})


@tool("list_connections",
      "Connected systems: each with its events, operations (reads and actions) and whether it is online.")
def list_connections(a):
    return call("GET", "/api/connectors")


@tool("get_settings", "Values of this installation that workflows refer to (addresses, time zone, ...).")
def get_settings(a):
    return call("GET", "/api/settings")


@tool("get_prices", "Prices and rates: what each model costs per million tokens, and what an hour of each role costs. "
      "Savings are counted with these.")
def get_prices(a):
    return call("GET", "/api/costs")


@tool("list_knowledge", "Places where the business's knowledge is kept: reachable or not, writable or not, how often "
      "used, what finding cost.")
def list_knowledge(a):
    return call("GET", "/api/knowledge")


@tool("search_knowledge", "What a workflow or an agent would be given for this question from one place of knowledge.",
      {"place": S, "query": S, "limit": I}, ["place", "query"])
def search_knowledge(a):
    return call("POST", "/api/knowledge/%s/search" % quote(a["place"]),
                {"query": a["query"], "limit": a.get("limit", 5)})


@tool("list_cases", "Cases (runs of workflows), newest first.",
      {"status": dict(S, description="e.g. STUCK, ESCALATED, AWAITING_APPROVAL, RESOLVED"),
       "attention": dict(B, description="only those waiting for a person"), "limit": I})
def list_cases(a):
    return call("GET", "/api/cases", query={"status": a.get("status"),
                                            "attention": str(a["attention"]).lower() if "attention" in a else None,
                                            "limit": a.get("limit", 20)})


@tool("get_case", "One case: its log, the actions it asked for and why, agent runs, and what started it. Each agent run has "
                  "`knowledge`: what the agent looked up (each search with its query, the entries it was given with how close "
                  "each was and a few lines of it, and what the search cost; on the step's own run, what the whole decision "
                  "looked up) and which of those entries it says its decision rests on (`applied`, with `why` in its words). "
                  "`appliedRecorded: false` means the decision was made before agents said this: what it was given is known, "
                  "what it relied on is not. `removed`: the entry was removed since; what is shown is what the agent was given. "
                  "A step where an agent handles the case towards a goal is under `leads`: the goal, the agent and its checker, "
                  "where it stands (`state`, `waitingFor` in words), its limits and what was used of them (`moves` of `maxMoves`, "
                  "`spent` of `spendLimit`, `deadline`), why it went to a person (`handedWhy`, `handedLimit`), and its `journal`: "
                  "each move with its number, kind, the agent's reason (`why`), what came of it (`result`, `bad`) and its cost, "
                  "with the news that came, people's answers and what the checker said.",
      {"id": S}, ["id"])
def get_case(a):
    return call("GET", "/api/cases/%s" % quote(a["id"]))


@tool("list_questions", "Questions agents that handle cases asked employees and that wait for an answer, oldest first: the "
      "case, the agent and its goal, the question and why it asks, whom it is for, and whether this access may answer it.")
def list_questions(a):
    return call("GET", "/api/questions")


@tool("answer_question",
      "Answer a question an agent that handles a case asked: the agent goes on with the answer. Only with the user's own "
      "answer, on their explicit word. Needs the right to work with cases.",
      {"caseId": S, "questionId": S, "answer": S}, ["caseId", "questionId", "answer"], read_only=False)
def answer_question(a):
    return call("POST", "/api/cases/%s/questions/%s" % (quote(a["caseId"]), quote(a["questionId"])), {"answer": a["answer"]})


@tool("draft_workflow_from_description",
      "Make a new workflow from a description in plain words: the installation's own assistant plans it from what the "
      "connections already offer, the installation checks the plan and keeps everything new (the workflow in test run, its "
      "agents, its approval rules) as one draft under Changes. Nothing is live until a person applies it. Answers at once "
      "with `status: making`; read it with get_workflow_launch until it is ready (`made`: what it holds in words), needs one "
      "thing (`missing`: an action no connection offers, which a person is asked to do meanwhile) or could not be made "
      "(`error`). Needs the right to propose changes.",
      {"description": S, "byHandMinutes": dict(I, description="how long the work takes a person today"),
       "byHandRole": dict(S, description="whose work it is, a role of the price list (get_prices); leave out for the usual one")},
      ["description"], read_only=False)
def draft_workflow_from_description(a):
    return call("POST", "/api/workflow-launches", {k: a[k] for k in ("description", "byHandMinutes", "byHandRole") if k in a})


@tool("get_workflow_launch",
      "Where a workflow made from a description got to: the draft and what it holds, what is missing, how trying it on "
      "examples went (`trial`, `tried`, `asExpected`), and when it was described, drafted, tried and put in test run. With "
      "workflow instead of id: how long that workflow took to launch and the changes made to it since.",
      {"id": S, "workflow": S})
def get_workflow_launch(a):
    if a.get("workflow"):
        return call("GET", "/api/case-types/%s/launch" % quote(a["workflow"]))
    if not a.get("id"):
        raise Refused("Say which: the id draft_workflow_from_description answered with, or the workflow's key")
    return call("GET", "/api/workflow-launches/%s" % quote(a["id"]))


@tool("try_workflow_draft",
      "Try a workflow made from a description on examples before it is applied: the latest events that would start it, "
      "each run with every action recorded and nothing sent, nothing kept. Read the outcome with get_workflow_launch. "
      "Needs the right to propose changes.",
      {"id": S}, ["id"], read_only=False)
def try_workflow_draft(a):
    return call("POST", "/api/workflow-launches/%s/try" % quote(a["id"]))


@tool("set_workflow_draft_senders",
      "Say which messages a workflow made from a description takes, where other workflows take messages of its "
      "connection too (`routing` of get_workflow_launch): the senders' domains or addresses. The draft brings rows of "
      "Who takes new messages that send these messages to it, applied with the rest of the draft on top of the rows "
      "there are. With `remove`, the rows are taken out of the draft instead. Use the user's own words for the senders; "
      "never make one up. Needs the right to propose changes.",
      {"id": S, "senderDomains": {"type": "array", "items": S, "description": "domains, \"@example.com\" or \"example.com\""},
       "senders": {"type": "array", "items": S, "description": "addresses"},
       "remove": dict(B, description="take the rows out of the draft")}, ["id"], read_only=False)
def set_workflow_draft_senders(a):
    if a.get("remove"):
        return call("POST", "/api/workflow-launches/%s/routing/remove" % quote(a["id"]))
    return call("POST", "/api/workflow-launches/%s/routing" % quote(a["id"]),
                {"senderDomains": a.get("senderDomains") or [], "senders": a.get("senders") or []})


@tool("explain_value",
      "Why a value of a case is what it is: its whole chain back to its sources, as a tree read from the top. Each "
      "node says in words where the node above came from (`says`, `detail`), when, at which step, what it cost "
      "(`cost`), and links to what it rests on: the answer a connected system gave then (`link.kind: read`, read it "
      "with get_kept_answer), the rules and the row that decided, the agent's run and the knowledge it relied on, the "
      "person who changed it and why (`reason`), the exact words an agent quoted and whether the platform found them "
      "(`quote`). `weak` marks a weak source. Take the value's name (and `commandId` as `action` for a value of an "
      "action) from list_weak_values or the case's values.",
      {"caseId": S, "name": dict(S, description="the value's name, as the case's values give it; a part of one such as order.customer.email is followed too"),
       "action": dict(S, description="the action the value is of (commandId); absent: a value of the case")},
      ["caseId", "name"])
def explain_value(a):
    return call("GET", "/api/cases/%s/values/explain" % quote(a["caseId"]), query={"name": a["name"], "action": a.get("action")})


@tool("list_weak_values",
      "The values of a case, each with where it came from in a few words, and apart in `weak` those whose source is "
      "weak: words an agent quoted that the platform did not find or that do not say the value (`quote`), a value a "
      "person changed without saying why (`reason`), an answer that was not kept (`source`). `kept: false` means the "
      "case ran before the platform kept where values came from. Use it to check a case value by value, then "
      "explain_value for any that matters.",
      {"caseId": S}, ["caseId"])
def list_weak_values(a):
    return call("GET", "/api/cases/%s/values" % quote(a["caseId"]))


@tool("get_kept_answer",
      "What a connected system answered to one read of a case, as it was then (the case's snapshot of its source): "
      "the connection, what was read and with what, when, and the answer. The id is a `read` link of explain_value.",
      {"caseId": S, "read": I}, ["caseId", "read"])
def get_kept_answer(a):
    return call("GET", "/api/cases/%s/reads/%s" % (quote(a["caseId"]), quote(a["read"])))


@tool("get_case_decisions",
      "What the rules decided in a case, each time a table was asked: what it saw and where each of those values came "
      "from, the table as it was with the row that decided (`row`, null when none fitted), what it said, and who "
      "last changed the table and why.",
      {"caseId": S}, ["caseId"])
def get_case_decisions(a):
    return call("GET", "/api/cases/%s/decisions" % quote(a["caseId"]))


@tool("report_correction",
      "Say a value of a case is wrong and how it should be, as a person would with “This is wrong” on the case: "
      "`name` as the case's values give it (and `action`, its commandId, for a value of an action), `value` how it "
      "should be, `reason` why in one line, `wrongIn` where it went wrong if known (agent, rules, knowledge, step, "
      "source for the other system itself, unknown; absent: where its chain says it came from). In the case it is "
      "used where it still can be: an action waiting for approval goes with it and still waits for whoever decides, a "
      "case still running goes on with it, a case that has gone further is kept as an example with it as the right "
      "answer; nothing done in another system is changed. Corrections of the same value in the same place become "
      "one problem for the Improver to draft a fix for. The answer says in `words` what it did. Only on the user's "
      "explicit word, with the value they gave. Needs the right to work with cases.",
      {"caseId": S, "name": dict(S, description="the value's name, as the case's values give it"),
       "action": dict(S, description="the action the value is of (commandId); absent: a value of the case"),
       "value": dict(S, description="how it should be, as the user said it"),
       "reason": dict(S, description="why, one line of up to 300 characters"),
       "wrongIn": dict(S, description="where it went wrong: agent, rules, knowledge, step, source or unknown")},
      ["caseId", "name", "value"], read_only=False, destructive=True)
def report_correction(a):
    body = {"name": a["name"], "value": a["value"]}
    for key in ("action", "reason", "wrongIn"):
        if a.get(key):
            body[key] = a[key]
    return call("POST", "/api/cases/%s/corrections" % quote(a["caseId"]), body)


@tool("list_corrections",
      "Values people said were wrong and how they should be, with what each did in the case (`used`) and how far "
      "fixing it went (`status`: OPEN nothing drafted yet, DRAFTED a change waits, FIXED applied, DECLINED the change "
      "was declined, NO_FIX the Improver found no change, SOURCE to be fixed in the other system), with the change "
      "(`changeId`) and the problem (`problemId`). With `caseId`, those of one case; without, the ones made with this "
      "access.",
      {"caseId": dict(S, description="the case; absent: the corrections made with this access")})
def list_corrections(a):
    if a.get("caseId"):
        return call("GET", "/api/cases/%s/corrections" % quote(a["caseId"]))
    return call("GET", "/api/corrections/mine")


@tool("get_redo_plan",
      "What doing a case that has ended again from one of its steps would do, with the values people corrected: the "
      "steps it went through, and for each action done after the step in another system what redoing does with it as "
      "its connection says (`does`: again; cancel-first, with the action that cancels it in `undo`; ask, sent again "
      "only when the user says so; stops, the redo cannot be done from there). `can` and `says` say whether and why "
      "not; `from` absent: right after the step that worked out the latest corrected value. `aiThen` is what those "
      "steps spent on AI the first time.",
      {"caseId": S, "from": dict(S, description="the step to do it again from, as `steps` names it")},
      ["caseId"])
def get_redo_plan(a):
    path = "/api/cases/%s/redo" % quote(a["caseId"])
    return call("GET", path, query={"from": a["from"]} if a.get("from") else None)


@tool("redo_case",
      "Do a case that has ended again from a step, as get_redo_plan says: what is to be cancelled first is cancelled "
      "in the other system, then the case runs again from the step with the values people corrected. What was sent "
      "goes out again only for the actions in `sendAgain` (their `command` from the plan). It acts in other systems "
      "for real: tell the user first what is cancelled and what is sent again, and do it only on their explicit word. "
      "Needs the right to work with cases.",
      {"caseId": S, "from": dict(S, description="the step to do it again from"),
       "sendAgain": {"type": "array", "items": S, "description": "actions sent before that are to go out again; absent: none"},
       "reason": dict(S, description="why, one line of up to 300 characters")},
      ["caseId", "from"], read_only=False, destructive=True)
def redo_case(a):
    body = {"from": a["from"], "sendAgain": list(a.get("sendAgain") or [])}
    if a.get("reason"):
        body["reason"] = a["reason"]
    return call("POST", "/api/cases/%s/redo" % quote(a["caseId"]), body)


@tool("list_approvals", "Actions in connected systems waiting for a person's decision, each with the reason given.")
def list_approvals(a):
    return call("GET", "/api/approvals")


@tool("list_case_files",
      "The files given to a case (a report, a scan, a book of figures), newest first: each with how far reading it went "
      "(`status`: waiting, reading, read, partly when some pages could not be read, failed, kept when nothing reads "
      "documents), how many `pages` and how they were read (`reading.readBy`: as text, as tables, by a model that reads "
      "pictures; `reading.unreadPages`), why in words (`problem`), what reading it cost (`cost.dollars`; `cost.unpriced` "
      "when a model it used has no price) and how many of its pages agents quoted for values of the case (`quotedPages`).",
      {"caseId": S}, ["caseId"])
def list_case_files(a):
    return call("GET", "/api/cases/%s/files" % quote(a["caseId"]))


@tool("give_case_file",
      "Give a case a file from this computer (a PDF, a scan, an Excel or Word file, a text; up to the installation's "
      "largest file, 100 MB unless it says otherwise). The connection that reads documents then reads it page by page; "
      "reading scanned pages with a model costs money, counted with the case. A workflow waiting for its documents goes on "
      "once the file is read. Only on the user's word. Needs the right to work with cases.",
      {"caseId": S, "path": dict(S, description="the file's path on this computer")}, ["caseId", "path"], read_only=False)
def give_case_file(a):
    return send_file("/api/cases/%s/files" % quote(a["caseId"]), a["path"])


@tool("read_case_file_again",
      "Have a file of a case read again: after it failed or was only partly read, or once a connection that reads "
      "documents has been added. Reading again costs again what reading cost. Needs the right to work with cases.",
      {"caseId": S, "file": dict(S, description="the file's id, from list_case_files")}, ["caseId", "file"], read_only=False)
def read_case_file_again(a):
    return call("POST", "/api/cases/%s/files/%s/read" % (quote(a["caseId"]), quote(a["file"])))


@tool("remove_case_file",
      "Remove a file from a case and from what the connection that read it remembers: agents can no longer find in it; "
      "values already quoted from it keep their quote. It cannot be undone (give the file again to have it back). Only on "
      "the user's explicit word. Needs the right to work with cases.",
      {"caseId": S, "file": dict(S, description="the file's id, from list_case_files")}, ["caseId", "file"], read_only=False,
      destructive=True)
def remove_case_file(a):
    return call("DELETE", "/api/cases/%s/files/%s" % (quote(a["caseId"]), quote(a["file"])))


@tool("search_case_files",
      "Find in the files of a case the pages that have what is asked, best first, as the case's agents find them: by "
      "their words and, where the installation is set up for it, by what they mean. Each page found has its file, number, "
      "the lines around what was found and the page's whole text, so it can be quoted with its page; `cost` is what "
      "finding cost, counted with the case.",
      {"caseId": S, "query": dict(S, description="what to look for, in plain words or the document's own"),
       "limit": dict(I, description="how many pages at most, default 5, at most 20"),
       "file": dict(S, description="only in this file of the case, by its id")}, ["caseId", "query"])
def search_case_files(a):
    return call("GET", "/api/cases/%s/files/search" % quote(a["caseId"]),
                query={"query": a["query"], "limit": a.get("limit"), "file": a.get("file")})


@tool("read_case_file_page",
      "One page of a file of a case, its whole text (tables as rows between bars), how it was read and, when it could not "
      "be, why.",
      {"caseId": S, "file": dict(S, description="the file's id, from list_case_files"), "page": I}, ["caseId", "file", "page"])
def read_case_file_page(a):
    return call("GET", "/api/cases/%s/files/%s/pages/%s" % (quote(a["caseId"]), quote(a["file"]), quote(a["page"])))


@tool("retry_case", "Send a stuck case on its way again: its failed step is tried once more. Needs the right to work "
      "with cases.", {"id": S}, ["id"], read_only=False)
def retry_case(a):
    return call("POST", "/api/cases/%s/retry" % quote(a["id"]))


@tool("decide_approval",
      "Approve or decline an action waiting for a decision. Approving lets it act in the connected system for real. "
      "To approve with some of its values changed, give them in `arguments` (only values the action has; the "
      "approval rules judge it again) and say why in `reason`, one line: it is kept with the case and with each value "
      "changed. Only on the user's explicit word. Needs the right to work with cases.",
      {"commandId": S, "approved": B,
       "arguments": {"type": "object", "description": "values of the action changed before approving, by name"},
       "reason": dict(S, description="why the values were changed, one line of up to 300 characters")},
      ["commandId", "approved"], read_only=False, destructive=True)
def decide_approval(a):
    body = {"approved": bool(a["approved"])}
    if a.get("arguments"):
        body["arguments"] = a["arguments"]
    if a.get("reason"):
        body["reason"] = a["reason"]
    return call("POST", "/api/approvals/%s" % quote(a["commandId"]), body)


@tool("list_repeated_changes",
      "Values people keep changing at the same step of the same workflow before they approve its action, the most "
      "changed first: how many times (`changes`), in how many cases, how many said why (`withReason`), and the latest "
      "changes from what to what, by whom and why. A correction people make again and again is a rule, an entry of "
      "the knowledge or an agent's instructions to put right: propose that change as a draft.",
      {"days": dict(I, description="how far back to look, 1 to 365; 30 unless said"),
       "least": dict(I, description="how many changes make a value repeated, at least 2")})
def list_repeated_changes(a):
    return call("GET", "/api/approvals/repeated-changes", query={"days": a.get("days"), "least": a.get("least")})


# problems: what is wrong with the installation or works badly in it, what it costs, and what was drafted about it

@tool("list_problems",
      "What is wrong with the installation or works badly in it now, the latest found first. Each problem says what "
      "is wrong (`what`), why (`cause`), what it costs (`worth`: work standing still, `perDay`, `perMonth`: work "
      "people do again or that is not saved, `overUsual`: AI spent above a usual day; `unpriced` when nobody said "
      "what the work is worth), where (`where`: workflow, step, agent, connection), what a person can open "
      "(`actions`), the drafts already made about it (`changes`, `draftWaiting`, each declined one with "
      "`declinedBecause`) and what the installation's own agent found in the cases behind it (`findings`, newest "
      "first: `common`, the `cases` it rests on, `missing`, `next` (change, new workflow or nothing), `change`, "
      "`newWorkflow` (name, startsOn, steps, the agents, rules, texts and connections it needs, handsToPerson), "
      "`saves` and `risks`). A finding is the starting point for changing a workflow or building a new one. Kinds include steps that keep failing, "
      "connections gone silent, AI spending far above usual, unusual situations that keep coming back, agents "
      "people often correct and workflows that hand many cases to people. Before drafting a change about one, look "
      "at its `changes`: somebody may be on it already.",
      {"gone": dict(B, description="also the problems that went away, after those there are now"), "limit": I})
def list_problems(a):
    return call("GET", "/api/problems", query={"gone": str(a["gone"]).lower() if "gone" in a else None,
                                               "limit": a.get("limit", 50)})


@tool("get_problem", "One problem as list_problems gives it, as it is now; `present: false` when it went away.",
      {"id": I}, ["id"])
def get_problem(a):
    return call("GET", "/api/problems/%s" % quote(a["id"]))


@tool("list_repeating_situations",
      "Unusual situations agents keep meeting since they last changed, the most frequent first: what blocked the "
      "agent (`blockers`), how many cases and how often, what it cost in AI and in people's time (`monthly`: by the "
      "month), the ways it can be closed for good (`ways`), and a draft already waiting (`draft`).")
def list_repeating_situations(a):
    return call("GET", "/api/situations/repeating")


@tool("close_situation",
      "Close a repeating situation for good in one of its `ways`: allow (the agent may do what it needed), permit (a "
      "person allows it once each time), wait (wait and ask again), instruct (tell the agent what to do; `text` "
      "required), knowledge (the question goes to the knowledge, `text` optional). Except for knowledge it writes a "
      "draft of the agent under Changes, made about the problem the situation is; nothing takes effect before the "
      "draft is applied.",
      {"key": dict(S, description="the situation's key from list_repeating_situations"),
       "way": {"type": "string", "enum": ["allow", "permit", "wait", "instruct", "knowledge"]},
       "text": S},
      ["key", "way"], read_only=False)
def close_situation(a):
    return call("POST", "/api/situations/close", {k: a[k] for k in ("key", "way", "text") if k in a})


@tool("test_run",
      "How a workflow in Test run is doing: how many of its cases a person compared with what they would have done "
      "and found the same, different or not needed, and whether switching it to Live would be asked about first. "
      "Without `workflow`: the test run cases waiting for a person to compare them.",
      {"workflow": dict(S, description="workflow key"), "limit": I})
def test_run(a):
    if a.get("workflow"):
        return call("GET", "/api/case-types/%s/test-run" % quote(a["workflow"]))
    return call("GET", "/api/test-runs/waiting", query={"limit": a.get("limit", 20)})


# drafts

def change(answer):
    """One change, with what must not be missed (can it be applied?) ahead of the long before and after."""
    if not isinstance(answer, dict) or "status" not in answer:
        return answer
    first = ("id", "kind", "target", "tenantId", "status", "problems", "warnings", "liveChanged", "note")
    lead = {k: answer[k] for k in first if k in answer}
    if answer.get("status") == "DRAFT":
        if answer.get("problems"):
            lead["canBeApplied"] = "no: fix the problems first (update_change)"
        elif answer.get("liveChanged"):
            lead["canBeApplied"] = "no: what is live changed since; propose it again from what is live now"
        else:
            lead["canBeApplied"] = "yes; nothing is live until it is applied"
    lead.update({k: v for k, v in answer.items() if k not in lead})
    return lead


@tool("list_change_kinds",
      "What can be changed through drafts: each kind with what it targets and what its payload carries. Read before "
      "the first propose_change.")
def list_change_kinds(a):
    return call("GET", "/api/changes/kinds")


@tool("propose_change",
      "Propose a change as a draft. Nothing live changes until the draft is applied. The draft is checked at once; "
      "its problems come back in the answer and it cannot be applied until they are fixed. Kinds and their payloads: "
      "list_change_kinds. `payload` is the same body the installation's own editor saves. For a workflow, fields left "
      "out are taken from what is live, so {shadow: true} alone switches it to Test run (actions recorded, not sent) "
      "and {shadow: false} makes it Live.",
      {"kind": dict(S, description="from list_change_kinds, e.g. workflow, rules, text, agent, connection, settings, "
                                   "prices, knowledge"),
       "target": dict(S, description="what is changed: workflow key, rules key, text key, agent key, connection "
                                     "name; for knowledge the place (new entry) or place/entryId"),
       "tenantId": S,
       "payload": {"type": "object"},
       "remove": B,
       "note": dict(S, description="why, in words the person who applies it will read"),
       "problem": dict(I, description="the problem the draft is about (id from list_problems): the draft then says "
                                      "why it was made, and the problem lists it"),
       "workbookPath": dict(S, description="for a calculation worked out by an Excel model: the model's path on this "
                                           "computer, sent as the payload's workbook")},
      ["kind", "note"], read_only=False)
def propose_change(a):
    body = {k: a[k] for k in ("kind", "target", "tenantId", "payload", "remove", "note", "problem") if k in a}
    if a.get("workbookPath"):
        body["payload"] = dict(body.get("payload") or {})
        body["payload"]["workbook"] = base64.b64encode(local_file(a["workbookPath"])[1]).decode("ascii")
    return change(call("POST", "/api/changes", body))


@tool("update_change", "Change a draft that is not applied yet (e.g. to fix its problems). Only drafts this access "
      "wrote.", {"id": S, "payload": {"type": "object"}, "remove": B, "note": S}, ["id"], read_only=False)
def update_change(a):
    body = {k: a[k] for k in ("payload", "remove", "note") if k in a}
    return change(call("PUT", "/api/changes/%s" % quote(a["id"]), body))


@tool("list_changes", "Drafts waiting to be applied, and the history of applied changes (by people and by agents). "
      "Each says the problem it was made about (`because`), when one was named.",
      {"status": {"type": "string", "enum": ["DRAFT", "APPLIED", "DISCARDED"]}, "kind": S, "target": S,
       "problem": dict(I, description="only the changes made about this problem"), "limit": I})
def list_changes(a):
    return call("GET", "/api/changes", query={k: a.get(k) for k in ("status", "kind", "target", "problem", "limit")})


@tool("get_change", "One change: what was live (before), what it makes (after), its problems and warnings, who made "
      "it and why, and liveChanged: true when what is live changed since, so applying would be refused.",
      {"id": S}, ["id"])
def get_change(a):
    return change(call("GET", "/api/changes/%s" % quote(a["id"])))


@tool("apply_change",
      "Make a draft live. Only with the user's explicit word for this draft, and only after showing them what it "
      "changes. Needs an access with the right to apply; without it, a person applies it in the Control Center. "
      "Refused while the draft has problems or when what is live changed since the draft was made. For a change of an "
      "agent, `askAgain` puts the cases it handed to people, still waiting, to it again once applied (it only decides; "
      "a person does what it says on each case); read how many and what it costs first with `GET "
      "/api/changes/{id}/ask-again` (read_api). For a change of a connection's files, a program of it that was "
      "running is started again on the new files by itself; `program` in the answer says what became of it and what, "
      "if anything, a person has to do.",
      {"id": S, "askAgain": B}, ["id"], read_only=False, destructive=True)
def apply_change(a):
    applied = change(call("POST", "/api/changes/%s/apply" % quote(a["id"]), {"askAgain": bool(a.get("askAgain"))}))
    if isinstance(applied, dict) and applied.get("kind") == "connection" and applied.get("status") == "APPLIED" \
            and not applied.get("removes"):
        try:
            applied["program"] = program_after_change(call("GET", "/api/connectors/%s/program" % quote(applied["target"])))
        except Refused as refused:
            applied["program"] = "What became of the connection's program could not be read: %s" % refused
    return applied


def program_after_change(p):
    """What became of a connection's program once a change of its files was applied, in the words a person reads."""
    if not isinstance(p, dict) or not p.get("ours"):
        return "Its program is not started by the installation: whoever runs it starts it again with the new files."
    if p.get("unavailable"):
        return "Programs are not started from this installation: whoever looks after it starts this one again with " \
               "the new files. " + p["unavailable"]
    state = p.get("state")
    if state == "failed":
        return "The program did not come up on the new files, so steps that use the connection fail; the change is " \
               "made. A person sees why on the connection's Program tab in the Control Center; put the change back " \
               "(revert_change) if the files are wrong."
    if state in ("stopped", "none"):
        return "The program is %s, so the new files take effect once a person starts it on the connection's Program " \
               "tab in the Control Center." % ("not started yet" if state == "none" else "stopped")
    if p.get("changed"):
        return "The program still runs on the earlier files: a person starts it again on the connection's Program tab " \
               "in the Control Center."
    if state == "starting":
        return "The program is starting again on the new files; it is running within a minute."
    return "The program runs on the new files: what the change adds works now."


@tool("discard_change", "Drop a draft that is not applied. `note` says why; it is kept with the draft, and whoever drafts "
      "the same thing next (a person or the installation's own agent) reads it.",
      {"id": S, "note": S}, ["id"], read_only=False)
def discard_change(a):
    return change(call("POST", "/api/changes/%s/discard" % quote(a["id"]), {"note": a["note"]} if a.get("note") else {}))


@tool("revert_change", "Propose putting back what was live before an applied change. Makes a draft; it still has to "
      "be applied.", {"id": S, "note": S}, ["id"], read_only=False)
def revert_change(a):
    return change(call("POST", "/api/changes/%s/revert" % quote(a["id"]), {"note": a["note"]} if a.get("note") else {}))


# examples and exams: a change tried on cases with a known right answer before it is applied

def exam(answer):
    """An exam with what matters first: how far it is, better or worse, what it cost; the long lists after."""
    if not isinstance(answer, dict) or "status" not in answer:
        return answer
    status = answer.get("status")
    said = {"running": "stopping after the example it is on; ask again with get_exam in a minute" if answer.get("stopping")
            else "still running: ask again with get_exam in a minute",
            "done": "finished", "stopped": "stopped before the end; what it found so far is below",
            "limit": "stopped at its spending limit; what it found so far is below",
            "failed": "could not be run: " + str(answer.get("error") or "")}.get(status, status)
    lead = {"exam": said, "name": answer.get("name"), "examplesDone": "%s of %s" % (answer.get("done"), answer.get("examples")),
            "spent": answer.get("spent"), "limit": answer.get("limit")}
    before, after = answer.get("before"), answer.get("after") or {}
    if not after.get("of"):
        lead["result"] = "not known yet: no example is finished"
    elif before:
        lead["result"] = "%s: right in %s of %s, it was %s" % (answer.get("verdict") or "not known yet", after.get("right"), after.get("of"),
                                                               before.get("right"))
    else:
        lead["result"] = "right in %s of %s, on what is live now" % (after.get("right"), after.get("of"))
    if answer.get("outdated"):
        lead["outOfDate"] = "the draft, what is live or the examples changed since; run it again to see how it does now"
    lead.update({k: v for k, v in answer.items() if k not in lead})
    return lead


@tool("list_examples",
      "The examples of an agent or a workflow: finished cases with a known right answer, which every change to it is "
      "tried on before it is applied. Also how its last exam went, how often the judge agrees with people, how many "
      "cases are worth adding, and what an exam would cost.",
      {"of": {"type": "string", "enum": ["agent", "workflow"]}, "key": dict(S, description="the agent's or workflow's key")},
      ["of", "key"])
def list_examples(a):
    return call("GET", "/api/examples", query={"of": a["of"], "key": a["key"]})


@tool("case_examples",
      "What a finished case can be an example of (each agent that decided in it, and its workflow), with what came in "
      "and the right answer taken from what a person approved, changed or declined; or why it cannot be one.",
      {"caseId": S}, ["caseId"])
def case_examples(a):
    return call("GET", "/api/cases/%s/examples" % quote(a["caseId"]))


@tool("add_example",
      "Keep a finished case as an example of an agent or a workflow. Its right answer is what a person made of it; "
      "say why it is right in `why` when it helps the judge. Needs an access that may propose changes.",
      {"caseId": S, "of": {"type": "string", "enum": ["agent", "workflow"]}, "key": S,
       "why": dict(S, description="optional: why the right answer is right, read by the judge")},
      ["caseId", "of", "key"], read_only=False)
def add_example(a):
    return call("POST", "/api/examples", {k: a.get(k) for k in ("caseId", "of", "key", "why")})


@tool("remove_example",
      "Take an example away: changes are no longer tried on it. The case itself stays. Only on the user's word.",
      {"id": dict(S, description="the example's id, from list_examples")}, ["id"], read_only=False, destructive=True)
def remove_example(a):
    return call("DELETE", "/api/examples/%s" % quote(a["id"]))


@tool("run_exam",
      "Try a change on the examples before it is applied: with `changeId`, a draft of an agent or a workflow next to "
      "what is live; with `of` and `key`, what is live alone. Nothing is sent anywhere; it costs money on AI, about "
      "what get_exam's estimate says, and stops at the installation's limit for one exam. A draft with 5 or more "
      "examples is tried by itself when it is saved. Needs an access that may propose changes.",
      {"changeId": S, "of": {"type": "string", "enum": ["agent", "workflow"]}, "key": S}, read_only=False)
def run_exam(a):
    if a.get("changeId"):
        return exam(call("POST", "/api/changes/%s/exam" % quote(a["changeId"])))
    if not a.get("of") or not a.get("key"):
        raise Refused("Say which draft to try (changeId), or which agent or workflow to try as it is live (of and key).")
    return exam(call("POST", "/api/exams", {"of": a["of"], "key": a["key"]}))


@tool("get_exam",
      "How an exam went: with `changeId`, the latest exam of that draft, what one would cost and why it cannot run "
      "yet if it cannot; with `id`, one exam. Better or worse than what is live, right in how many examples, what got "
      "worse and better with the right answer beside it, and what it cost. Tell the user before they apply a draft.",
      {"changeId": S, "id": S})
def get_exam(a):
    if a.get("changeId"):
        answer = call("GET", "/api/changes/%s/exam" % quote(a["changeId"]))
        if isinstance(answer, dict) and isinstance(answer.get("exam"), dict):
            answer["exam"] = exam(answer["exam"])
        return answer
    if a.get("id"):
        return exam(call("GET", "/api/exams/%s" % quote(a["id"])))
    raise Refused("Say which draft (changeId) or which exam (id).")


@tool("stop_exam", "Stop a running exam after the example it is on; what it found so far stays.",
      {"id": S}, ["id"], read_only=False)
def stop_exam(a):
    return exam(call("POST", "/api/exams/%s/stop" % quote(a["id"])))


@tool("read_api",
      "Read anything else the installation's HTTP API offers (GET only), e.g. /api/knowledge/{place}/entries or "
      "/api/results?days=7. Use the specific tools first.",
      {"path": dict(S, description="starts with /api/")}, ["path"])
def read_api(a):
    path = a["path"]
    if not path.startswith("/api/") or ".." in path:
        raise Refused("path must start with /api/")
    return call("GET", path)


# --- building configurations from the schema ------------------------------------------------------------------
# Workflows, solutions and connections are built as data and checked against the schema (what a configuration may
# say: agentric-os-schema), the installation's own when it is reached, else the copy carried in schema/. Files are
# written only from what passed; the installation checks once more when a draft is proposed.

import build_connection  # noqa: E402
import build_rules  # noqa: E402
import build_solution  # noqa: E402
import build_workflow  # noqa: E402
import schema_files  # noqa: E402


def _fetch_schema(name):
    if name == "index.json":
        return call("GET", "/api/schema")
    return call("GET", "/api/schema/file", query={"name": name})


SCHEMA = schema_files.Schema(_fetch_schema)


def schema_said(answer):
    answer["builtOn"] = SCHEMA.where()
    return answer


def write_files(folder, written):
    """Writes files into a folder of this computer; says which it wrote."""
    folder = os.path.abspath(os.path.expanduser(str(folder or "").strip()))
    if not folder or folder == os.path.abspath(os.sep):
        raise Refused("Say which folder to write into: its path on this computer.")
    paths = []
    for name, text in written.items():
        path = os.path.join(folder, *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            file.write(text)
        paths.append(path)
    return paths


@tool("schema",
      "What a configuration may say, as the installation runs it (or as this plugin carries it when no installation "
      "is reached): its version and its files. Give `file` to read one, such as blocks.json, workflow.json, "
      "solution.schema.json, connector.schema.json, connector-contract.json, runner.json. Building tools use it "
      "themselves; read it to understand what can be built.",
      {"file": dict(S, description="a file the schema lists; left out: what the schema holds")})
def schema(a):
    name = a.get("file")
    if not name:
        return schema_said({"schema": SCHEMA.file("index.json")})
    if name == "camel/yaml-dsl.schema.json":
        raise Refused("That is Camel's own schema of its YAML DSL, which check_connection checks routes against; it "
                      "is too large to read whole. Ask for one EIP, language or data format with camel_doc instead.")
    try:
        return schema_said({"file": name, "says": SCHEMA.file(name)})
    except LookupError:
        raise Refused("The schema has no file %s. Its files: schema with no file." % name)


@tool("connection_kinds",
      "Kinds of connection (a channel, a place knowledge is kept, a calendar, ...): a connection that says it is of a "
      "kind (provides:) offers the actions and tells the events every one of that kind does, so workflows written for "
      "the kind work with it. Read before building a connection of a known kind.")
def connection_kinds(a):
    return schema_said({"kinds": SCHEMA.file("connection-kinds.json")})


@tool("connection_contract",
      "What the routes of a connection rely on and keep to: the address of each operation's route, how to refuse, "
      "events pulled and pushed, idempotency, settings, helpers; the Camel version of the connections' program and "
      "the components it carries. Read before writing routes.")
def connection_contract(a):
    return schema_said({"contract": SCHEMA.file("connector-contract.json"), "program": SCHEMA.file("runner.json"),
                        "description": "connector.schema.json (read it with schema) is the format of connector.yaml"})


@tool("camel_components",
      "Apache Camel components for a connection's routes, from Camel's own catalog of the version the connections' "
      "program runs: name, what it is for, how its address is written, and whether the program has it built in or "
      "the platform's image carries it (others are fetched once when the connection first starts).",
      {"search": dict(S, description="words to look for in the name or what it is for")})
def camel_components(a):
    runner = SCHEMA.file("runner.json")
    built, carried = set(runner["builtIn"]), set(runner["carried"]["camel"])
    words = str(a.get("search") or "").lower().split()
    found = []
    for c in SCHEMA.file("camel/index.json")["components"]:
        text = (c["name"] + " " + (c.get("title") or "") + " " + (c.get("description") or "")).lower()
        if all(w in text for w in words):
            found.append(dict(c, has="built in" if c["name"] in built else "carried by the image" if c["name"] in carried else "fetched when first started"))
    return schema_said({"camelVersion": runner["camelVersion"], "components": found})


@tool("camel_doc",
      "One part of Apache Camel as its catalog describes it: a component (its address and every option: which are "
      "required, the values it takes, defaults, which are secrets), an EIP (a step of a route: setHeader, choice, "
      "split, ...), a language (simple, jq, ...) or a data format (jackson, csv, ...). Read it before using that part "
      "in a route.",
      {"kind": {"type": "string", "enum": ["component", "eip", "language", "dataformat"]}, "name": S}, ["kind", "name"])
def camel_doc(a):
    folder = {"component": "components", "eip": "eips", "language": "languages", "dataformat": "dataformats"}[a["kind"]]
    try:
        return schema_said({"camelVersion": SCHEMA.file("runner.json")["camelVersion"],
                            a["kind"]: SCHEMA.file("camel/%s/%s.json" % (folder, a["name"]))})
    except LookupError:
        listed = [c["name"] for c in SCHEMA.file("camel/index.json")[folder]]
        raise Refused("There is no %s “%s” here. The ones there are: %s" % (a["kind"], a["name"], ", ".join(listed)))


CONNECTION_ARGS = {
    "description": {"type": "object", "description": "the connection's description, shaped like connector.yaml "
                                                     "(connector.schema.json)"},
    "routes": {"type": "array", "items": {"type": "object"},
               "description": "its routes, shaped like Camel's YAML DSL: [{route: {id, from: {uri, parameters, "
                              "steps}}}]; left out for a connection whose program comes from elsewhere"},
}


@tool("check_connection",
      "Check a connection before anything is written or proposed: its description against the schema and against "
      "itself (what is promised, read back, cancelled), its routes against Camel's YAML DSL and catalog (every "
      "address and option), and both against the contract with the platform (a route for every operation, settings "
      "named and never written in, refusals, events, no code). Says each problem with where it is; `ready` is true "
      "when nothing stops it.", CONNECTION_ARGS, ["description"])
def check_connection(a):
    return schema_said(build_connection.check(SCHEMA, a["description"], a.get("routes")).answer())


@tool("write_connection",
      "Write a connection's files into a folder of this computer, connector.yaml and routes/<name>.camel.yaml, once "
      "check_connection finds nothing that stops it. Nothing is written while it has problems.",
      dict(CONNECTION_ARGS, folder=dict(S, description="the folder of the connection on this computer")),
      ["folder", "description"], read_only=False)
def write_connection(a):
    report = build_connection.check(SCHEMA, a["description"], a.get("routes"))
    if report.problems:
        return schema_said(report.answer(written=[], said="Nothing was written: fix the problems first."))
    written = write_files(a["folder"], build_connection.files(a["description"], a.get("routes")))
    return schema_said(report.answer(written=written))


@tool("propose_connection",
      "Propose a new or changed connection to the installation as a draft, once check_connection finds nothing that "
      "stops it: the installation checks it again, and a person applies it under Changes. Keeping its files starts "
      "nothing; a person starts its program on the connection's page and enters its settings there.",
      dict(CONNECTION_ARGS, note=dict(S, description="why, in words the person who applies it will read")),
      ["description", "note"], read_only=False)
def propose_connection(a):
    report = build_connection.check(SCHEMA, a["description"], a.get("routes"))
    if report.problems:
        return schema_said(report.answer(said="Not proposed: fix the problems first."))
    written = build_connection.files(a["description"], a.get("routes"))
    routes = next((text for name, text in written.items() if name.startswith("routes/")), None)
    looked = call("POST", "/api/connectors/check-file", {"file": written["connector.yaml"], "routes": routes})
    payload = {"file": written["connector.yaml"]}
    if routes is not None:
        payload["routes"] = routes
    draft = change(call("POST", "/api/changes", {"kind": "connection", "target": a["description"]["connector"],
                                                 "payload": payload, "note": a["note"]}))
    return schema_said(report.answer(installationSays=looked, draft=draft))


def known_here(solution=None, folder=None, connections=None):
    """What a workflow or a solution may name: what the installation has, when it is reached, and what the solution
    being built brings. A list the installation could not give is left out, so it is not checked against."""
    known = {}
    try:
        connectors = call("GET", "/api/connectors")
        known["connections"] = {c["name"]: {o.get("name"): o.get("kind", "command") for o in (c.get("manifest") or {}).get("operations") or []}
                                for c in connectors}
        for c in connectors:
            for source in (c.get("manifest") or {}).get("eventSources") or []:
                if source.get("operation"):
                    known["connections"][c["name"]].setdefault(source["operation"], "read")
        known["agents"] = {a["key"] for a in call("GET", "/api/agents")}
        known["texts"] = {t["key"] for t in call("GET", "/api/texts")}
        known["rules"] = {d["key"] for d in call("GET", "/api/decisions")}
        known["calculations"] = {c["key"] for c in call("GET", "/api/calculation-sheets")}
    except Refused:
        known = {}
    for description in connections or []:
        if isinstance(description, dict) and description.get("connector"):
            offered = {o.get("name"): o.get("kind", "command") for o in description.get("operations") or [] if isinstance(o, dict)}
            known.setdefault("connections", {})[description["connector"]] = offered
    if isinstance(solution, dict):
        for name, items in (("agents", "agents"), ("texts", "texts"), ("calculations", "calculations")):
            if solution.get(items):
                known[name] = set(known.get(name) or set()) | {i.get("key") for i in solution[items] if isinstance(i, dict)}
        if folder and solution.get("decisions"):
            rules = set(known.get("rules") or set())
            for name in solution["decisions"]:
                path = os.path.join(os.path.abspath(os.path.expanduser(folder)), *str(name).split("/"))
                if os.path.isfile(path):
                    try:
                        rules.update(build_solution.decisions_in(path))
                    except Exception:
                        pass
            known["rules"] = rules
    return known


PLAN = {"type": "object", "description": "the workflow as a plan: {key, name, about, start, byHandMinutes, byHandRole, "
        "steps: [...]}; a step is a block {id, name, block, fields, next, onError: {CODE: step}, timer: {after | at, to}}, "
        "a choice {id, name, choose: [{when: \"${...}\", to}, {otherwise: true, to}]}, values worked out {id, name, set: "
        "{name: \"${...}\"}, next}, a person's approval {id, name, approval: true, next}, or an end {id, end: true}; any "
        "step may say byHandMinutes, byHandRole, brings"}
BUILDING_CONTEXT = {
    "solution": {"type": "object", "description": "the solution the workflow belongs to, shaped like solution.yaml: "
                                                  "its agents, texts, calculations and rules count as there"},
    "folder": dict(S, description="the solution's folder on this computer, where its rules (DMN files) are"),
    "connections": {"type": "array", "items": {"type": "object"},
                    "description": "descriptions of connections being built alongside, shaped like connector.yaml"},
}


@tool("workflow_parts",
      "What a workflow is built from: the blocks (what each does, its fields, which are required, how it can end) and "
      "what else a workflow may contain (choices, values worked out, approvals, timers, the values a case starts with, "
      "how values are named, the helpers of expressions, what the work is worth). Read before making a plan.")
def workflow_parts(a):
    return schema_said({"blocks": SCHEMA.file("blocks.json"), "workflow": SCHEMA.file("workflow.json")})


@tool("check_workflow_plan",
      "Check a workflow made as a plan of blocks, before it is written or proposed: every block and field, every way "
      "(nothing leads nowhere, every step comes to an end), every error caught one the step can end with, every value "
      "used worked out before on every way to it, and every connection, operation, agent, text, table of rules and "
      "calculation named one the installation or the solution has. Says each problem with where it is.",
      dict(BUILDING_CONTEXT, plan=PLAN), ["plan"])
def check_workflow_plan(a):
    known = known_here(a.get("solution"), a.get("folder"), a.get("connections"))
    return schema_said(build_workflow.check(SCHEMA, a["plan"], known).answer(checkedNames=sorted(known)))


@tool("write_workflow",
      "Write a workflow made as a plan into a solution's folder of this computer, as workflows/<key>.bpmn20.xml, once "
      "check_workflow_plan finds nothing that stops it. The BPMN is written from the plan; nothing is written while "
      "the plan has problems. Gives the processKey and the file for solution.yaml.",
      dict(BUILDING_CONTEXT, plan=PLAN, folder=dict(S, description="the solution's folder on this computer")),
      ["plan", "folder"], read_only=False)
def write_workflow(a):
    known = known_here(a.get("solution"), a.get("folder"), a.get("connections"))
    report = build_workflow.check(SCHEMA, a["plan"], known)
    if report.problems:
        return schema_said(report.answer(written=[], said="Nothing was written: fix the problems first."))
    solution = (a.get("solution") or {}).get("solution")
    name = "workflows/%s.bpmn20.xml" % a["plan"]["key"]
    written = write_files(a["folder"], {name: build_workflow.bpmn(SCHEMA, a["plan"], solution)})
    return schema_said(report.answer(written=written, forSolutionYaml={"key": a["plan"]["key"], "name": a["plan"]["name"],
                                                                     "process": name, "processKey": a["plan"]["key"]}))


@tool("propose_workflow",
      "Propose a workflow made as a plan to the installation as a draft, in Test run (it records what it would do and "
      "sends nothing), once check_workflow_plan finds nothing that stops it. The installation checks it again; a "
      "person applies it under Changes. Going Live is its own later change. A workflow of a new solution, whose agents, "
      "texts or rules are new too, goes with propose_solution.",
      dict(plan=PLAN, note=dict(S, description="why, in words the person who applies it will read"),
           startOn={"type": "array", "items": S, "description": "the types of event that open a case of it"},
           startWhen=dict(S, description="an expression an event must also meet"),
           policy=dict(S, description="the table of rules that judges its commands"),
           tags={"type": "array", "items": S}),
      ["plan", "note"], read_only=False)
def propose_workflow(a):
    known = known_here()
    report = build_workflow.check(SCHEMA, a["plan"], known)
    if report.problems:
        return schema_said(report.answer(said="Not proposed: fix the problems first."))
    xml = build_workflow.bpmn(SCHEMA, a["plan"])
    looked = call("POST", "/api/case-types/validate", {"processKey": a["plan"]["key"], "bpmnXml": xml})
    payload = {"name": a["plan"]["name"], "processKey": a["plan"]["key"], "bpmnXml": xml, "shadow": True,
               "startOn": a.get("startOn") or []}
    for said, key in (("startWhen", "startWhen"), ("policy", "policyDecisionKey"), ("tags", "tags")):
        if a.get(said):
            payload[key] = a[said]
    draft = change(call("POST", "/api/changes", {"kind": "workflow", "target": a["plan"]["key"], "payload": payload, "note": a["note"]}))
    return schema_said(report.answer(installationSays=looked, draft=draft))


RULES_PLAN = {"type": "object", "description": "the table as a plan: {key, name, about, noRowFits, policy: true when it "
              "judges a workflow's commands, inputs: [{label, looksAt, type, about}], outputs: [{name, label, type}], rows: "
              "[{when: [one cell per input: a value, null for any, or {compare: \"> 100\"}], then: [one value per output], why}]}"}


@tool("check_rules",
      "Check a table of rules made as a plan (what a decide step asks, or the policy that judges a workflow's commands) "
      "the way the engine will read it: every column looks at a named value, every row has a cell per column and a "
      "value per output, every cell is one the engine can read, a policy answers AUTO, NEEDS_APPROVAL or DENY.",
      {"plan": RULES_PLAN}, ["plan"])
def check_rules(a):
    return schema_said(build_rules.check(SCHEMA, a["plan"]).answer(howRulesAreRead=SCHEMA.file("rules.json")))


@tool("write_rules",
      "Write a table of rules made as a plan into a solution's folder as rules/<key>.dmn, once check_rules finds "
      "nothing that stops it. Gives the file to list under decisions in solution.yaml.",
      {"plan": RULES_PLAN, "folder": dict(S, description="the solution's folder on this computer"),
       "solution": dict(S, description="the solution's name")}, ["plan", "folder"], read_only=False)
def write_rules(a):
    report = build_rules.check(SCHEMA, a["plan"])
    if report.problems:
        return schema_said(report.answer(written=[], said="Nothing was written: fix the problems first."))
    name = "rules/%s.dmn" % a["plan"]["key"]
    written = write_files(a["folder"], {name: build_rules.dmn(a["plan"], a.get("solution"))})
    return schema_said(report.answer(written=written, forSolutionYaml={"decisions": [name]}))


@tool("propose_rules",
      "Propose a new or changed table of rules made as a plan to the installation as a draft, once check_rules finds "
      "nothing that stops it. Changing a table changes every workflow that asks it: list_rules says who asks it.",
      {"plan": RULES_PLAN, "note": dict(S, description="why, in words the person who applies it will read")},
      ["plan", "note"], read_only=False)
def propose_rules(a):
    report = build_rules.check(SCHEMA, a["plan"])
    if report.problems:
        return schema_said(report.answer(said="Not proposed: fix the problems first."))
    draft = change(call("POST", "/api/changes", {"kind": "rules", "target": a["plan"]["key"],
                                                 "payload": {"dmnXml": build_rules.dmn(a["plan"])}, "note": a["note"]}))
    return schema_said(report.answer(draft=draft))


SOLUTION_ARGS = {
    "folder": dict(S, description="the solution's folder on this computer: solution.yaml goes there, the files it names are there"),
    "solution": {"type": "object", "description": "the solution shaped like solution.yaml (solution.schema.json)"},
    "connections": BUILDING_CONTEXT["connections"],
}


@tool("check_solution",
      "Check a whole solution in its folder the way the installation will install it: solution.yaml against the "
      "schema, every file it names there, each workflow's process the key it says, every agent, text, table of rules, "
      "calculation and connection's operation that a workflow or an agent names one the solution or the installation "
      "has, reads given to agents as tools and commands as commands.", SOLUTION_ARGS, ["folder", "solution"])
def check_solution(a):
    known = known_here(None, None, a.get("connections"))
    return schema_said(build_solution.check(SCHEMA, a["folder"], a["solution"], known).answer(checkedAgainst=sorted(known)))


@tool("write_solution",
      "Write solution.yaml into the solution's folder once check_solution finds nothing that stops it.",
      SOLUTION_ARGS, ["folder", "solution"], read_only=False)
def write_solution(a):
    known = known_here(None, None, a.get("connections"))
    report = build_solution.check(SCHEMA, a["folder"], a["solution"], known)
    if report.problems:
        return schema_said(report.answer(written=[], said="Nothing was written: fix the problems first."))
    return schema_said(report.answer(written=write_files(a["folder"], {"solution.yaml": build_solution.solution_yaml(a["solution"])})))


@tool("propose_solution",
      "Propose a whole solution from its folder to the installation, as drafts a person applies under Changes: its "
      "connections, tables of rules, texts, agents and workflows (in Test run), in the order they are to be applied, "
      "once check_solution and check_connection find nothing that stops it. Calculations are proposed with "
      "propose_change (kind calculation).",
      {"folder": SOLUTION_ARGS["folder"], "solution": SOLUTION_ARGS["solution"],
       "connections": {"type": "array", "items": {"type": "object"},
                       "description": "the solution's new or changed connections: [{description, routes}]"},
       "note": dict(S, description="why, in words the person who applies the drafts will read")},
      ["folder", "solution", "note"], read_only=False)
def propose_solution(a):
    folder = os.path.abspath(os.path.expanduser(a["folder"]))
    solution = a["solution"]
    connections = [c for c in a.get("connections") or [] if isinstance(c, dict)]
    found = {"connections": {}}
    for c in connections:
        report = build_connection.check(SCHEMA, c.get("description"), c.get("routes"))
        if report.problems:
            found["connections"][str((c.get("description") or {}).get("connector"))] = report.problems
    report = build_solution.check(SCHEMA, folder, solution, known_here(None, None, [c.get("description") for c in connections]))
    if report.problems or any(found["connections"].values()):
        return schema_said(report.answer(connectionProblems=found["connections"], said="Nothing was proposed: fix the problems first."))

    def read(name):
        with open(os.path.join(folder, *str(name).split("/")), encoding="utf-8") as f:
            return f.read()

    def ref(said):
        connector, operation = said.split(".", 1)
        return {"connector": connector, "operation": operation}

    drafts = []

    def propose(kind, target, payload, what):
        answer = change(call("POST", "/api/changes", {"kind": kind, "target": target, "payload": payload, "note": a["note"]}))
        drafts.append({"what": what, "draft": answer.get("id"), "problems": answer.get("problems") or []})

    for c in connections:
        written = build_connection.files(c["description"], c.get("routes"))
        payload = {"file": written["connector.yaml"]}
        routes = next((t for n, t in written.items() if n.startswith("routes/")), None)
        if routes is not None:
            payload["routes"] = routes
        propose("connection", c["description"]["connector"], payload, "the connection %s" % c["description"]["connector"])
    for name in solution.get("decisions") or []:
        for key in build_solution.decisions_in(os.path.join(folder, *name.split("/"))):
            propose("rules", key, {"dmnXml": read(name)}, "the rules %s" % key)
    for text in solution.get("texts") or []:
        propose("text", text["key"], {"name": text.get("name") or text["key"], "text": read(text["file"])}, "the text %s" % text["key"])
    for agent in build_solution.agents_in_order(solution.get("agents")):
        payload = {k: v for k, v in agent.items() if k not in ("key", "tools", "commands")}
        payload["tools"] = [ref(t) for t in agent.get("tools") or []]
        payload["commands"] = [ref(t) for t in agent.get("commands") or []]
        payload["helpers"] = [h if isinstance(h, dict) else {"agent": h} for h in agent.get("helpers") or []]
        payload.setdefault("name", agent["key"])
        propose("agent", agent["key"], payload, "the agent %s" % agent["key"])
    for case_type in solution.get("caseTypes") or []:
        payload = {"name": case_type.get("name") or case_type["key"], "processKey": case_type["processKey"],
                   "bpmnXml": read(case_type["process"]), "shadow": True, "startOn": case_type.get("startOn") or []}
        for said, key in (("startWhen", "startWhen"), ("policy", "policyDecisionKey"), ("tags", "tags"), ("schedule", "schedule"),
                          ("takesFrom", "takesFrom"), ("whoDecides", "whoDecides"), ("enabled", "enabled")):
            if case_type.get(said) is not None:
                payload[key] = case_type[said]
        if case_type.get("tenantId"):
            payload["tenantId"] = case_type["tenantId"]
        propose("workflow", case_type["key"], payload, "the workflow %s, in Test run" % case_type["key"])
    return schema_said({"drafts": drafts, "applyInThisOrder": [d["what"] for d in drafts],
                        "said": "The drafts are under Changes. Problems a draft names that another draft of this "
                                "solution settles go away once that one is applied first, in this order.",
                        "notProposed": ["the calculation %s: propose_change kind calculation" % c["key"] for c in solution.get("calculations") or []]})


# --- MCP over stdio --------------------------------------------------------------------------------------------

def handle(message):
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "agentric-os", "version": VERSION},
                "instructions": "Agentric OS installation. Look first (overview, list_*), then propose changes as "
                                "drafts with a note saying why; check workflows with check_workflow before "
                                "proposing; apply only on the user's explicit word. To build a connection, a workflow "
                                "or a solution, never write its files by hand: read connection_contract or "
                                "workflow_parts, make it as data, check it (check_connection, check_workflow_plan, "
                                "check_solution) until it is ready, then write or propose it. Show money where it matters: "
                                "what a change costs, saves or earns."}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{k: v for k, v in t.items() if k != "run"} for t in TOOLS]}
    if method == "tools/call":
        found = next((t for t in TOOLS if t["name"] == params.get("name")), None)
        if found is None:
            raise LookupError("unknown tool: %s" % params.get("name"))
        arguments = params.get("arguments") or {}
        missing = [name for name in found["inputSchema"]["required"] if arguments.get(name) in (None, "")]
        if missing:
            return {"content": [{"type": "text", "text": "%s needs: %s" % (found["name"], ", ".join(missing))}],
                    "isError": True}
        try:
            result = found["run"](arguments)
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=1)}],
                    "isError": False}
        except Refused as refused:
            return {"content": [{"type": "text", "text": str(refused)}], "isError": True}
        except KeyError as missing:
            return {"content": [{"type": "text", "text": "missing argument: %s" % missing}], "isError": True}
    if method in ("resources/list",):
        return {"resources": []}
    if method in ("prompts/list",):
        return {"prompts": []}
    raise NotImplementedError(method)


def main():
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            out.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                  "error": {"code": -32700, "message": "parse error"}}) + "\n")
            out.flush()
            continue
        if "id" not in message:  # a notification: nothing to answer
            continue
        try:
            reply = {"jsonrpc": "2.0", "id": message["id"], "result": handle(message)}
        except NotImplementedError as error:
            reply = {"jsonrpc": "2.0", "id": message["id"],
                     "error": {"code": -32601, "message": "method not found: %s" % error}}
        except LookupError as error:
            reply = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32602, "message": str(error)}}
        except Exception as error:  # never let one call take the server down
            reply = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32603, "message": repr(error)}}
        out.write(json.dumps(reply, ensure_ascii=False) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
