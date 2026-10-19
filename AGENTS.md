# Guide for AI agents: the plugin

Read this before changing anything here. `README.md` is what a user of the plugin reads.

## What this is

Agentric OS (by Aglium) is an agentic operations platform: it watches what happens in a business's systems of
record, decides, and acts through them, and it is set up for any business by configuration, not code.

This repository is a Claude Code plugin and a stdio MCP server in plain Python (3.9+, standard library only)
over the HTTP API of an Agentric OS installation.

**Why it is built this way.** An installation's owner can let their own AI agent look after it, safely: the
agent reads, and what it changes is a draft that goes live only when applied by somebody with the right. The
installation decides what a token may do; the plugin only passes things on.

| Where | What |
|---|---|
| `server/agentric_os_mcp.py` | The MCP server: tools over the installation's API |
| `server/test_agentric_os_mcp.py` | Its checks (standard library `unittest`) |
| `skills/agentric-os/SKILL.md` | How an agent should work with an installation |
| `commands/` | `/agentric-status`, `/agentric-change` |
| `.claude-plugin/`, `.mcp.json` | Plugin and marketplace descriptions |
| `LICENSE` | The terms of use: clients of Aglium only, under their agreement |
| `.gitlab-ci.yml` | The checks on every push, and the release made for a tag |

Try it against an installation with its address and a token (created in the Control Center under AI agent
access) set the way the README says: `/plugin configure agentric-os@aglium` in Claude Code, or the file
`~/.config/agentric-os/config.json`. Use an installation meant for trying things, never one that runs a
business.

## Checks

```bash
python3 -m py_compile server/agentric_os_mcp.py
python3 -m unittest discover -s server -v
claude plugin validate .
```

## Rules

- **Standard library only.** No dependency to install is a promise of the README.
- **No rights of its own.** The plugin never decides what is allowed and never works around a refusal; a
  refusal is passed on in the installation's own words. Sign-in details of connections are never reachable.
- **Changes are drafts.** A tool that changes something makes a draft and says first whether it can be
  applied; nothing reaches around drafts and history.
- **General tools only**: workflows, rules, texts, agents, connections, knowledge, cases, results. No tool
  for one line of business.
- **Plain words.** No engine statuses, keys, HTTP codes or stack traces in what a person reads; an answer
  says what happened and what to do next. Nobody is left with an error and no way on.
- **Answers lead with what matters to the owner**, with what it costs and what it is worth where the
  installation gives it.
- **The token is a secret**: never printed, logged or committed, and no instruction puts it on a command
  line or into `export` (both end up in the terminal's history). Claude Code asks for it (`userConfig` in
  `plugin.json`, marked sensitive) and hands it over in the environment; any other client keeps it in
  `~/.config/agentric-os/config.json`, which the server refuses to read when others can read it.
- **Nothing of a real organisation is committed**: no names of customers, no addresses of real installations,
  no real data. Examples use `ops.example.com`.
- When the installation's API changes, this server, the skill and the README change in the same piece of work.

## Versions and releases

The version is in `.claude-plugin/plugin.json` and, the same, in `VERSION` of `server/agentric_os_mcp.py`. A
release is a tag named exactly as that version (`0.1.0`); the pipeline refuses a tag that says otherwise.

## Before a commit to master

A commit to `master` (or `main`) is made only when everything this repository checks is green on exactly what
is being committed. Not after the commit, not "CI will tell":

1. Run every check below, the same ones `.gitlab-ci.yml` runs. In a shared checkout other agents' uncommitted
   changes are in the tree, so run them on a copy holding `HEAD` plus your own changes only; a result that includes someone else's half-done work says nothing about your commit.
2. Commit only when all of it passes. A failing test or check is fixed, or the commit waits and the owner is
   told what fails and why. Never skip, disable or loosen a test to get a commit through, and never wave a
   failure away as "not mine": if it is red on `master`, say so and fix it or stop.
3. A change other repositories depend on is also checked where it is used (below): a contract changed here
   and left unchecked there turns their checks red without anyone touching them.
4. The `CHANGELOG.md` line goes into the same commit (see "Changelog").

```bash
python3 -m py_compile server/agentric_os_mcp.py server/test_agentric_os_mcp.py ci/release.py
python3 -m unittest discover -s server -v
python3 ci/release.py check
claude plugin validate .
```

## Changelog

`CHANGELOG.md` is kept by hand and is part of every change: a commit that changes what the repository does
adds a line under `Unreleased`, in the same commit. Write what changed for whoever uses the plugin, in plain
words, not which files were touched. At a release the `Unreleased` block gets the version and the date.

Commit messages are one sentence saying what is now true, in plain words.
