#!/usr/bin/env python3
"""What the pipeline asks about versions and releases. Standard library only.

    ci/release.py check     the plugin and its server say the same version; for a tag, the tag says it too
    ci/release.py notes     for a tag: that version's part of the changelog, for its GitHub release
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*path):
    with open(os.path.join(ROOT, *path), encoding="utf-8") as file:
        return file.read()


def versions():
    plugin = json.loads(read(".claude-plugin", "plugin.json")).get("version", "")
    found = re.search(r'^VERSION = "([^"]+)"', read("server", "agentric_os_mcp.py"), re.MULTILINE)
    return plugin, found.group(1) if found else ""


def check():
    plugin, server = versions()
    tag = os.environ.get("GITHUB_REF_NAME", "") if os.environ.get("GITHUB_REF_TYPE") == "tag" else ""
    print("Version in .claude-plugin/plugin.json: %s; in server/agentric_os_mcp.py: %s; tag: %s"
          % (plugin or "none", server or "none", tag or "none"))
    if not re.fullmatch(r"\d+\.\d+\.\d+", plugin):
        sys.exit("The version in .claude-plugin/plugin.json should read like 1.2.3, and it reads '%s'." % plugin)
    if plugin != server:
        sys.exit("The plugin says version %s and its server says %s. Make VERSION in server/agentric_os_mcp.py "
                 "and \"version\" in .claude-plugin/plugin.json the same." % (plugin, server))
    if tag and tag != plugin:
        sys.exit("The tag is %s but the plugin says version %s. A release is tagged exactly as the version: "
                 "change \"version\" in .claude-plugin/plugin.json (and VERSION in the server) to %s, or tag %s."
                 % (tag, plugin, tag, plugin))


def notes(version):
    """The changelog's part for this version; what is still under Unreleased when the version has no part yet."""
    parts = re.split(r"^## +", read("CHANGELOG.md"), flags=re.MULTILINE)[1:]
    by_title = {part.split("\n", 1)[0].strip(): part.split("\n", 1)[1].strip() for part in parts if "\n" in part}
    for title, text in by_title.items():
        if title.split(" ")[0].strip("[]") == version:
            return text
    return by_title.get("Unreleased", "")


def release_notes():
    tag = os.environ.get("GITHUB_REF_NAME", "") if os.environ.get("GITHUB_REF_TYPE") == "tag" else ""
    if not tag:
        sys.exit("Release notes are made for a tag only, and this run is not for one.")
    print(notes(tag) or "Agentric OS plugin %s" % tag)


if __name__ == "__main__":
    {"check": check, "notes": release_notes}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
