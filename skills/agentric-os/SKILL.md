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
| Cases | Runs of workflows | `list_cases`, `get_case`, `list_approvals` |
| Examples and exams | Cases with a known right answer, and changes tried on them before they are applied | `list_examples`, `case_examples`, `add_example`, `remove_example`, `run_exam`, `get_exam`, `stop_exam` |

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
7. **Keep it general.** Express the business through these parts. Do not invent special fields or hard-code one
   customer's data in a process: put values in settings, wording in texts, decisions in rules.

## Things the tools refuse on purpose

- Sign-in details of connections (passwords, keys) are never readable or changeable with an access token. A person
  sets them in the Control Center.
- Approving an action that touches a real system (`decide_approval`) and retrying a case act for real. Do them only when
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
