# Changelog

What changed in this repository, newest first. Kept by hand: every change that alters what the repository
does adds a line under Unreleased in the same commit (see AGENTS.md).

## Unreleased

- The skill says that `get_change` tells how a change made about a problem works since it was applied (`watch`), and that one that made things worse comes back as a problem with a draft putting it back. **Needs:** a core that watches applied changes (`agentric-os`).

- `apply_change` takes `askAgain`: for a change of an agent, the cases it handed to people that still wait are put to it again once applied, and each shows what it would do for a person to do with one press. **Needs:** a core that asks again after a change (`agentric-os`).

- `AGENTS.md`: every document is in English, and reports, screenshots and notes made while working stay out of `docs/`.

- `AGENTS.md` says the README, the skill and the changelog change in the same commit as a tool, and that the server calls only what the installation's API description has.

- `list_problems` and `get_problem` say what the installation's own agent found in the cases behind a problem (`findings`: what they have in common, the cases, what is missing, what to change or the outline of a new workflow, what it should save, what to watch) and which draft waits about it; the skill says to start from the newest finding and how to build a new workflow from its outline as a draft in Test run. **Needs:** a core that keeps findings with problems (`agentric-os`).

- `discard_change` takes `note`, why the draft is declined; it is kept with the draft, and a change says it as `declinedBecause`. **Needs:** a core that keeps why a draft was declined (`agentric-os`).

- An agent can work on what works badly in the installation: `list_problems` and `get_problem` (what is wrong or works badly, what it costs, including by the month, where, and what was drafted about it), `list_repeating_situations` and `close_situation` (unusual situations agents keep meeting, closed for good by a draft), `test_run` (how a workflow in Test run compares with people). `propose_change` takes `problem`, the problem the draft is about, and `list_changes` gives the changes made about one. The skill says how to go from a problem to a draft tried on its examples, with what it costs and should save. **Needs:** a core with `GET /api/problems` and `problem` on drafts (`agentric-os`).

- `get_case` says that each agent run carries `knowledge`: what the agent looked up, the entries it was given and which of them it says its decision rests on, with why and what the lookups cost; the skill says to look there when asked why an agent decided as it did, and that entries found often and never applied are the ones to put right. Needs an installation whose core gives `knowledge` with a case's agent runs (`agentric-os`).

- `AGENTS.md` says that a commit to `master` is made only when every check of the repository, the same ones CI runs, passes on exactly what is committed, and which checks of other repositories a change here also needs.

- A change to an agent or a workflow can be tried on its examples before it is applied: `list_examples` (the examples, the last exam, how often the judge agrees with people, the cases worth adding, what an exam costs), `case_examples` and `add_example` (keep a finished case with its right answer), `remove_example`, `run_exam` (a draft next to what is live, or what is live alone), `get_exam` (better, worse or the same, right in how many, what got worse, what it cost) and `stop_exam`; an exam not finished says so, and one asked to stop says it is stopping. The skill asks to try a draft and say how it went before asking to apply it. Needs an installation with examples and exams, and an access that may propose changes (`agentric-os`).

- The plugin says under what terms it may be used: by clients of Aglium only, under their agreement with Aglium, to work with their own installation (`LICENSE`, named in the README and in the plugin's description for Claude Code). It contains no third-party code.
- The plugin is installed from its public repository: `claude plugin marketplace add https://gitlab.com/aglium/aglium_os/agentric-os-plugin.git`, then `claude plugin install agentric-os@aglium` and `/plugin configure agentric-os@aglium`. A newer version is taken with `claude plugin marketplace update aglium`.
- Every push is checked: the server compiles, its checks pass (where the address and the token are taken from, the environment before the file; a file other people can read is refused; what is missing is said in plain words), and Claude Code accepts the plugin. A tag makes a GitLab release named as the tag, and is refused when it is not the version the plugin says.
- The plugin starts in Claude Code before the address and the access token are set: both are optional, and a tool called without them says what is missing and where to enter it (`/plugin configure agentric-os@aglium`) instead of the plugin failing to load.
- The address and the access token are asked for by Claude Code and kept by it (the token in the computer's protected store), so they outlive a restart and are never typed into a command. Any other MCP client reads them from `~/.config/agentric-os/config.json`, refused when other people can read the file.
- Refusals are passed on in the installation's own words (which right is missing, revoked or expired token); a change's answer leads with whether it can be applied; required arguments are asked for before calling.
- Agentric OS plugin for Claude Code: MCP server over the installation's HTTP API with an access token, skill and commands.
