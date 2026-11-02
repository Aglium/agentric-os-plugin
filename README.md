# Agentric OS for Claude Code

Look after an installed Agentric OS from your own AI agent: see what it does and what it is worth, and propose
changes to workflows, rules, texts, agents, connections and knowledge. Every change is a draft; it goes live only when
it is applied, by a person in the Control Center or by the agent if its access has the right to apply.

## What you need

- Python 3.9 or newer (nothing else to install: the server uses only the standard library).
- An access token: Control Center, **AI agent access**, **Create access**. Choose what it may do:
  look, suggest changes, apply changes, work with cases. The page that shows the token also shows the steps
  below, filled in for your installation.

The token is a secret. It is never typed into a command (commands stay in the terminal's history) and never
exported in a terminal: it is pasted once where it is asked for, and kept where only you can read it.

## Claude Code

1. In a terminal:

   ```bash
   claude plugin marketplace add Aglium/agentric-os-plugin
   claude plugin install agentric-os@agentric-os
   ```

2. Open Claude Code and type `/plugin configure agentric-os@agentric-os`.
3. When it asks, paste the **Installation address** (for example `https://ops.example.com`) and the
   **Access token**.

Claude Code keeps both from then on: the address in its settings, the token in the protected store of the
computer (the Keychain on a Mac). Nothing has to be entered again after the terminal or the computer restarts.
The same command changes them later; `claude plugin configure agentric-os@agentric-os` in a terminal says which of the
two are set, without showing them.

Neither has to be set for the plugin to start. Until both are, every question about the installation is
answered with what is missing and where to enter it.

The Control Center shows the same commands ready to copy, next to a new access token.

To get a newer version of the plugin later:

```bash
claude plugin marketplace update agentric-os
```

To take the plugin away again, with what it kept: `claude plugin uninstall agentric-os@agentric-os` and
`claude plugin marketplace remove agentric-os`.

Then ask in your own words, or use `/agentric-status` and `/agentric-change <what should change>`.

## Any other MCP client (Cursor, Codex, Claude Desktop, ...)

1. Make a file that only you can read:

   ```bash
   mkdir -p ~/.config/agentric-os
   touch ~/.config/agentric-os/config.json
   chmod 600 ~/.config/agentric-os/config.json
   ```

2. Open `~/.config/agentric-os/config.json` in a text editor and put the address and the token in:

   ```json
   {
     "url": "https://ops.example.com",
     "token": "<paste the access token here>"
   }
   ```

3. Add `server/agentric_os_mcp.py` to the assistant's list of MCP servers, as a stdio server. Nothing secret is
   in these settings:

   ```json
   {
     "mcpServers": {
       "agentric-os": {
         "command": "python3",
         "args": ["/path/to/agentric_os_mcp.py"]
       }
     }
   }
   ```

A file that other people on the computer can read is refused, and the server says so: run the `chmod 600` again.
To change the address or the token, edit the file. To take it away: `rm -r ~/.config/agentric-os` and remove the
server from the assistant's list.

Where the server looks, each value on its own: first the environment variables `AGENTRIC_OS_URL` and
`AGENTRIC_OS_TOKEN` (this is how Claude Code hands over what it keeps; a secret manager can do the same), then
the file.

On Windows the command is usually `python` or `py`.

## What it can and cannot do

The agent looks at workflows, rules, texts, agents, connections, knowledge, cases and results; proposes changes as
drafts; and, before a change to an agent or a workflow is applied, tries it on that one's examples (finished cases
with a known right answer) and says whether it got better or worse and what the trying cost. Trying sends nothing
anywhere and stops at the installation's limit for one exam. An access that may propose changes may also keep
examples and run exams.

It also sees what is wrong with the installation or works badly in it, and what each problem costs: steps that keep
failing, connections gone silent, agents people often correct, workflows that hand many cases to people, unusual
situations that keep coming back, and how a workflow in Test run compares with people. A draft it makes about a
problem names that problem, so the person who applies it sees why it was made and, afterwards, whether the problem
went away.

It can make a new workflow from a description in plain words, as one draft built only from what your connections already
offer, try it on recent events without sending anything, and say how long it took to launch.

For a case an agent handles towards a goal, it reads how the agent is handling it (each move, why, what came of it
and what it cost), sees the questions such agents ask employees, and answers one on your word.

What the agent may do is decided by the installation, from the rights of the access. Sign-in details of connections
are never available to it. A revoked or expired token stops working at once.

## Checking a change to the plugin

```bash
python3 -m unittest discover -s server -v
claude plugin validate .
```

`CHANGELOG.md` says what changed in each version.

## License

For clients of Aglium only: the plugin may be used under your agreement with Aglium, to work with your own
Agentric OS installation. See [LICENSE](LICENSE). It contains no third-party code.
