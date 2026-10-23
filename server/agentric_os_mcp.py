#!/usr/bin/env python3
"""MCP server for an installed Agentric OS.

Lets a local AI agent (Claude Code, Cursor, Codex, ...) look at an installation and propose changes to it through
the installation's HTTP API, with an access token the installation issued (Control Center, "AI agent access").

Only the Python standard library is used, so nothing has to be installed besides Python 3.9+. It speaks MCP over
stdio: one JSON-RPC message per line.

Where the installation is and the access token are taken, each on its own, from the first place that has them:

    1. the environment: AGENTRIC_OS_URL (e.g. https://ops.example.com) and AGENTRIC_OS_TOKEN (aos_...). Claude Code
       fills these in from what it asked for and keeps (`/plugin configure agentric-os@aglium`).
    2. the file ~/.config/agentric-os/config.json: {"url": "https://ops.example.com", "token": "aos_..."}. It holds a
       secret, so it is read only when nobody but its owner can read it (chmod 600).

What the agent may do is decided by the installation, not here: a token that may only look cannot propose, and a
token without the right to apply cannot make a draft live. Every change is a draft first; nothing an agent
proposes runs until it is applied.
"""

import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

VERSION = "0.1.0"
PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
TIMEOUT = float(os.environ.get("AGENTRIC_OS_TIMEOUT", "60"))


class Refused(Exception):
    """An answer the agent should read as what went wrong, not as a crash."""


# --- the installation's HTTP API -------------------------------------------------------------------------------

CONFIG_FILE = os.path.join("~", ".config", "agentric-os", "config.json")
HOW_TO_SET = ("In Claude Code type `/plugin configure agentric-os@aglium` and paste them when asked. For any other "
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
    base, token = where_and_token()
    url = base + path
    if query:
        query = {k: v for k, v in query.items() if v is not None and v != ""}
        if query:
            url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", "agentric-os-mcp/" + VERSION)
    if data is not None:
        request.add_header("Content-Type", "application/json")
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


@tool("list_agents", "AI agent profiles: instructions, model, the reads they may use, the actions they may propose.")
def list_agents(a):
    return call("GET", "/api/agents")


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


@tool("list_approvals", "Actions in connected systems waiting for a person's decision, each with the reason given.")
def list_approvals(a):
    return call("GET", "/api/approvals")


@tool("retry_case", "Send a stuck case on its way again: its failed step is tried once more. Needs the right to work "
      "with cases.", {"id": S}, ["id"], read_only=False)
def retry_case(a):
    return call("POST", "/api/cases/%s/retry" % quote(a["id"]))


@tool("decide_approval",
      "Approve or decline an action waiting for a decision. Approving lets it act in the connected system for real. "
      "Only on the user's explicit word. Needs the right to work with cases.",
      {"commandId": S, "approved": B}, ["commandId", "approved"], read_only=False, destructive=True)
def decide_approval(a):
    return call("POST", "/api/approvals/%s" % quote(a["commandId"]), {"approved": bool(a["approved"])})


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
                                      "why it was made, and the problem lists it")},
      ["kind", "note"], read_only=False)
def propose_change(a):
    body = {k: a[k] for k in ("kind", "target", "tenantId", "payload", "remove", "note", "problem") if k in a}
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
      "/api/changes/{id}/ask-again` (read_api).",
      {"id": S, "askAgain": B}, ["id"], read_only=False, destructive=True)
def apply_change(a):
    return change(call("POST", "/api/changes/%s/apply" % quote(a["id"]), {"askAgain": bool(a.get("askAgain"))}))


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
                                "proposing; apply only on the user's explicit word. Show money where it matters: "
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
