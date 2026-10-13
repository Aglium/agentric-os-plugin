#!/usr/bin/env python3
"""What the pipeline asks about versions and releases. Standard library only.

    ci/release.py check     the plugin and its server say the same version; for a tag, the tag says it too
    ci/release.py publish   for a tag: a GitLab release named as the tag, with that version's part of the changelog
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

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
    tag = os.environ.get("CI_COMMIT_TAG", "")
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


def publish():
    tag = os.environ.get("CI_COMMIT_TAG", "")
    if not tag:
        sys.exit("A release is made for a tag only, and this pipeline is not for one.")
    check()
    address = "%s/projects/%s/releases" % (os.environ["CI_API_V4_URL"], os.environ["CI_PROJECT_ID"])
    body = {"tag_name": tag, "name": tag, "description": notes(tag) or "Agentric OS plugin %s" % tag}
    request = urllib.request.Request(address, data=json.dumps(body).encode("utf-8"), method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("JOB-TOKEN", os.environ["CI_JOB_TOKEN"])
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            print("Release %s is published: %s" % (tag, json.load(response).get("_links", {}).get("self", "")))
    except urllib.error.HTTPError as error:
        said = error.read().decode("utf-8", "replace")
        if error.code == 409 or "already exists" in said:
            print("Release %s is already there; nothing to do." % tag)
            return
        sys.exit("GitLab did not make the release %s (answer %d): %s" % (tag, error.code, said))


if __name__ == "__main__":
    {"check": check, "publish": publish}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
