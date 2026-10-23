---
name: agentric-os
description: Use when the user wants to see, set up or change their Agentric OS installation (workflows, rules, texts, AI agents, connections, knowledge, prices, cases) through the agentric-os tools.
---

# Working on an Agentric OS installation

An installation watches the business's systems, decides and acts through them. It is configured, not coded: everything
is made of the same few general parts, and you change them through the `agentric-os` tools.

| Part | What it is | Tools |
|---|---|---|
| Workflow | A process (BPMN) built from blocks: read from a system, ask rules, ask an agent, write a text, act, hand to a person | `list_workflows`, `get_workflow`, `list_blocks`, `check_workflow` |
| Rules | Tables (DMN) workflows ask; a workflow's policy table decides which actions need a person's approval | `list_rules`, `get_rules` |
| Texts | A shared library; a workflow names the texts it writes from | `list_texts`, `get_text` |
| Agents | AI profiles: instructions, the reads they may use, the actions they may propose | `list_agents` |
| Connections | Connected systems with their events and operations | `list_connections` |
| Knowledge | Places where the business keeps what it knows | `list_knowledge`, `search_knowledge` |
| Prices and rates | Model prices and what an hour of each role costs | `get_prices` |
| Cases | Runs of workflows | `list_cases`, `get_case`, `list_approvals`, `list_questions`, `answer_question` |
| New workflows | Made from a description | `draft_workflow_from_description`, `get_workflow_launch`, `try_workflow_draft`, `set_workflow_draft_senders` |
| Examples and exams | Cases with a known right answer, and changes tried on them before they are applied | `list_examples`, `case_examples`, `add_example`, `remove_example`, `run_exam`, `get_exam`, `stop_exam` |
| Problems | What is wrong with the installation or works badly in it, what it costs, and what was drafted about it | `list_problems`, `get_problem` |
| Unusual situations and Test run | Situations agents keep meeting, and how a workflow in Test run compares with people | `list_repeating_situations`, `close_situation`, `test_run` |

## How to work

1. **Look first.** Start with `overview`. Before changing anything, read what is there now (`get_workflow`, `get_rules`,
   `get_text`) and who uses it: a text or a table of rules is shared, so a change reaches every workflow that names it
   (`list_texts` shows where each text is used).
2. **Every change is a draft.** `propose_change` stores it and changes nothing live. Always give a `note` saying why,
   in words the person who applies it will read. Fix problems the draft comes back with (`update_change`).
3. **Check workflows before proposing them.** Build processes only from the blocks `list_blocks` describes, run
   `check_workflow`, fix every error. A new workflow goes in as Test run (`shadow: true`): it records the actions it
   would take and sends none. Going Live is its own, later change.
4. **Try a change on its examples before it is applied.** An agent or a workflow may have examples: finished cases
   with a known right answer (`list_examples`). A draft of it is tried on them, by itself when there are 5 or more,
   or with `run_exam` (it costs money on AI, within the installation's limit for one exam, and sends nothing). Read
   the result with `get_exam` and tell the user plainly: better, worse or the same, right in how many, what got
   worse, what it cost. Worse does not forbid applying, but say it before asking. Cases a person changed or declined
   are worth adding as examples (`case_examples`, `add_example`).
5. **Apply only on the user's explicit word**, for that draft, after showing what it changes (`get_change` shows what
   was live and what it makes) and how its exam went. If the access has no right to apply, say the draft is ready and that a person applies
   it in the Control Center under Changes.
6. **Say what it is worth.** The installation counts money everywhere: what model calls cost, the people's time saved,
   what is earned. When you add or change steps, set how long a person would take (`byHandMinutes`) and whose work it is
   (`byHandRole`, from `get_prices`) so savings are counted, and tell the user what the change should cost and save.
   After a change has run, compare with `overview`.
7. **Why an agent decided as it did.** `get_case` shows, for each agent run, what it looked up in the knowledge
   (`knowledge.lookups`) and which entries it says it relied on (`entries` with `applied: true`, and `why`). Entries
   that are found again and again but never applied, or that are applied with a wrong reading, are the ones to put
   right (`search_knowledge`, `list_knowledge`, then a `knowledge` change). What the lookups cost is in `knowledge.spent`.
8. **Improve what works badly.** `list_problems` says what costs money now: steps that keep failing, but also agents
   people often correct and workflows that hand many cases to people, priced by the month. For one of them: read its
   `changes` first (somebody may be on it), open its cases (the `actions` say where; `list_cases`, `get_case`) and find
   what they have in common, then draft the smallest change that would settle such cases, with `problem` set to the
   problem's id so the draft says why it was made. Add the corrected cases as examples (`case_examples`,
   `add_example`) and try the draft on them before asking anybody to apply it. Tell the user what the problem costs a
   month, what the draft changes, how its exam went, and what it should save. Once the change has run, `get_problem`
   says whether the problem is gone, and `get_change` says how the change works since it was applied (`watch`: the
   share before and since, better, the same or worse, and what it saves or loses a month). One that made things worse
   becomes a problem of its own, with a draft that puts it back.
   The installation's own agent may have been there first: a problem's `findings` say what the cases have in common,
   which cases show it, what is missing and what to do next. Start from the newest finding instead of reading the
   cases again. With `next: new workflow`, its `newWorkflow` outline is what to build: take the events it starts on
   and the operations from `list_connections`, build the steps from `list_blocks`, `check_workflow` it, and propose it
   as a `workflow` draft in Test run (`shadow: true`) with `problem` set. A draft declined before says why
   (`declinedBecause`): do not propose the same again.
9. **A case an agent handles towards a goal.** `get_case` gives such a step under `leads`: the goal, what it waits
   for, its limits, and the journal of its moves with the agent's reasons, results and costs. A question it asked an
   employee waits in `list_questions`; answer one (`answer_question`) only with the user's own answer. To build such a
   workflow, use the step “Agent handles the case” from `list_blocks` with an agent, a goal, a checker that reads what
   shows the goal, and limits, and propose it in Test run.
10. **A new workflow from a description.** When the user describes work in plain words, `draft_workflow_from_description`
   makes the whole thing as one draft (the workflow in test run with an agent that handles each case, its agents, its
   approval rules) from what the connections already offer; read it with `get_workflow_launch`. Say what it holds and,
   when something is `missing`, that a person will be asked to do it until a connection offers it. Try it with
   `try_workflow_draft`, then the user applies it under Changes. Where other workflows take messages of the same
   connection, `routing` says which of them it takes (rows of Who takes new messages applied with it) or what the
   assistant asks; set the senders the user names, or take the rows out, with `set_workflow_draft_senders`.
   `get_workflow_launch` with `workflow` says how long it took to launch.
11. **Keep it general.** Express the business through these parts. Do not invent special fields or hard-code one
   customer's data in a process: put values in settings, wording in texts, decisions in rules.

## Things the tools refuse on purpose

- Sign-in details of connections (passwords, keys) are never readable or changeable with an access token. A person
  sets them in the Control Center.
- Approving an action that touches a real system (`decide_approval`), answering an agent's question (`answer_question`) and retrying a case act for real. Do them only when
  the user asks for that specific one.
- A refusal says what is missing (a right on the access, a problem in the draft, something changed meanwhile). Tell
  the user plainly; do not try a way around it.

## Changes, by kind

`list_change_kinds` says what can be changed and what each kind's `payload` carries; read it before the first
`propose_change`. The usual ones: `workflow` (target: workflow key; fields left out stay as they are live, so
`{shadow: true}` alone is a draft that switches it to Test run), `rules` (`{dmnXml}`), `text`, `agent`, `connection`
(description and routes files; keeping them starts nothing), `settings`, `prices`, `knowledge` (target: the place for a
new entry, `place/entryId` for an existing one). `remove: true` drafts taking something away where the kind allows it.

To undo an applied change, `revert_change` makes a draft that puts back what was there.
