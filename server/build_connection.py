"""Builds a connection (its description, connector.yaml, and its routes in Camel's YAML DSL) and checks it the way the
installation and the connections' program will, before anybody tries it.

A connection is given as data: the description as an object shaped like connector.yaml, the routes as a list shaped
like Camel's YAML DSL. It is checked against the schema: the description's format and what holds it together, the
routes against Camel's own YAML DSL schema and catalog (every address, every option), and both against the contract
the routes keep with the platform (a route for every operation, settings named and never written in, refusals,
events, no code in routes). What is wrong is said with where it is; YAML is written only from what passed.
"""
import re

import schema_check
import yaml_out

PLACE = re.compile(r"\{\{(\?)?([^}:]+)(:[^}]*)?\}\}")
SCRIPTS = {"groovy", "js", "java", "joor", "python", "mvel", "ognl", "spel", "csimple", "datasonnet"}
ENDPOINT_STEPS = {"to", "toD", "enrich", "pollEnrich", "wireTap", "poll"}
NESTED = ("steps", "when", "otherwise", "doCatch", "doFinally")


class Report:
    def __init__(self):
        self.problems = []
        self.warnings = []

    def problem(self, where, what):
        self.problems.append({"where": where, "what": what})

    def warn(self, where, what):
        self.warnings.append({"where": where, "what": what})

    def answer(self, **more):
        result = {"ready": not self.problems, "problems": self.problems, "worthKnowing": self.warnings}
        result.update(more)
        return result


def check(schema, description, routes):
    """What is wrong with a connection and what is worth knowing, as a report."""
    report = Report()
    if not isinstance(description, dict):
        report.problem("description", "has to be the description as an object, shaped like connector.yaml")
        return report
    for where, what in schema_check.problems(schema.file("connector.schema.json"), description):
        report.problem("description" + ("." + where if where else ""), what)
    _hold_together(schema, description, report)
    if routes is None:
        report.warn("routes", "There are no routes: the connection's program has to come from elsewhere, and says itself what it offers")
        return report
    if not isinstance(routes, list):
        report.problem("routes", "have to be a list of routes, shaped like Camel's YAML DSL: [{route: {id, from: {uri, steps}}}]")
        return report
    for where, what in schema_check.problems(schema.file("camel/yaml-dsl.schema.json"), routes, scalars_as_text=True):
        report.problem("routes" + where if where.startswith("[") else "routes." + where, what)
    _routes_keep_the_contract(schema, description, routes, report)
    return report


def files(description, routes):
    """The connection's files as text: connector.yaml and the routes."""
    written = {"connector.yaml": "# What this connection offers. Built with the agentric-os plugin.\n" + yaml_out.dump(description)}
    if routes is not None:
        written["routes/%s.camel.yaml" % description.get("connector")] = yaml_out.dump(routes)
    return written


# --- the description -------------------------------------------------------------------------------------------

def _named(items):
    return [item for item in items if isinstance(item, dict) and isinstance(item.get("name"), str)]


def _hold_together(schema, description, report):
    operations = _named(description.get("operations") or [])
    names = [o["name"] for o in operations]
    reads = {o["name"] for o in operations if o.get("kind") == "read"}
    # the operation a source is pulled with is one of its reads, whether or not operations lists it
    reads |= {s["operation"] for s in description.get("eventSources") or [] if isinstance(s, dict) and s.get("operation")}
    for name in set(n for n in names if names.count(n) > 1):
        report.problem("description.operations", "The action “%s” is listed twice. Keep one" % name)
    for o in operations:
        at = "description.operations[%s]" % o["name"]
        command = o.get("kind") != "read"
        if not o.get("label") and not o.get("description"):
            report.warn(at, "has no label or description, so people will see its bare name")
        redo = o.get("redo")
        if command and redo is None:
            report.warn(at, "does not say what doing it again means (redo: sets | creates | sends): it will not be done again when a case is redone from a step")
        if not command and redo is not None:
            report.warn(at, "only looks something up, so redo says nothing for it")
        if isinstance(redo, dict):
            undo = (redo.get("undo") or {}).get("operation")
            if undo and undo not in names:
                report.problem(at, "is to be cancelled with “%s”, which is not among its actions" % undo)
        verify = (o.get("verify") or {}).get("operation") if isinstance(o.get("verify"), dict) else None
        if verify and verify not in reads:
            report.problem(at, "is to be checked with “%s”, which is not one of its reads" % verify)
    check_with = (description.get("check") or {}).get("operation") if isinstance(description.get("check"), dict) else None
    if check_with and check_with not in reads:
        report.problem("description.check", "The connection is to be tried with “%s”, which is not one of its reads" % check_with)
    sources = [s for s in description.get("eventSources") or [] if isinstance(s, dict)]
    told = set()
    pushed = []
    for s in sources:
        told.update(e for e in s.get("events") or [] if isinstance(e, str))
        if not s.get("operation"):
            pushed.append(s.get("name"))
    for e in [e for e in description.get("events") or [] if isinstance(e, dict)]:
        at = "description.events[%s]" % e.get("type")
        if e.get("type") and e["type"] not in told:
            report.warn(at, "is described, but no source of events lists it, so it never arrives")
        if e.get("readWith") and e["readWith"] not in reads:
            report.problem(at, "says its details are read with “%s”, which is not one of its reads" % e["readWith"])
        if not e.get("label"):
            report.warn(at, "has no label, so people choosing what a workflow starts on see its bare type")
    kinds = {k["key"]: k for k in schema.file("connection-kinds.json")}
    for kind in description.get("provides") or []:
        known = kinds.get(kind)
        if known is None:
            report.warn("description.provides", "“%s” is not a kind of connection the platform knows: %s" % (kind, ", ".join(kinds)))
            continue
        for needed in known.get("needs") or []:
            if needed not in names:
                report.warn(
                    "description.provides", "It says it is %s, but has no action “%s”, which every one offers" % (known["label"].lower(), needed))
        for event in known.get("tells") or []:
            if event not in told:
                (report.problem if kind == "channel" else report.warn)(
                    "description.provides", "It says it is %s, but no source of events tells “%s”" % (known["label"].lower(), event))
    if not names and not sources:
        report.problem("description", "offers nothing: no action under operations and no event under eventSources")
    if "setup" in description:
        report.warn("description.setup", "is said by the connection's program when it runs; leave it out")
    return pushed


# --- the routes ------------------------------------------------------------------------------------------------

def _routes_keep_the_contract(schema, description, routes, report):
    name = description.get("connector") or ""
    contract = schema.file("connector-contract.json")
    runner = schema.file("runner.json")
    camel = schema.file("camel/index.json")
    settings = set(description.get("configuration") or [])
    optional = set(description.get("optionalConfiguration") or [])
    promised = [o["name"] for o in _named(description.get("operations") or [])]
    promised += [s["operation"] for s in description.get("eventSources") or [] if isinstance(s, dict) and s.get("operation")]
    pushed = [s.get("name") for s in description.get("eventSources") or [] if isinstance(s, dict) and not s.get("operation")]
    refusals = set(contract["failures"]["refusals"].values())
    helpers = {h["bean"] for h in contract.get("helpers") or []}
    carried = set(runner["builtIn"]) | set(runner["carried"]["camel"])
    components = {c["name"]: c for c in camel["components"]}
    answered, ids = {}, []
    publishes = []

    def endpoint(uri, parameters, at, dynamic):
        if not isinstance(uri, str) or not uri:
            return
        if "?" in uri and not dynamic:
            report.problem(at, "The options of “%s” are written into the address; put them under parameters:" % uri)
        scheme = uri.split(":", 1)[0]
        if dynamic and ("${" in scheme or "{{" in scheme or uri.startswith("${")):
            named = [c for c in description.get("components") or [] if c in components]
            if not named:
                report.warn(at, "The component is only known when the route runs: name it under components: in the description")
                return
            # an address put together from a setting, such as {{shop.base-url}}/orders: its options are those of the component named
            scheme, uri = named[0], named[0] + ":" + uri
        signs_in = sorted(k for k in (parameters or {}) if str(k) in ("authMethod", "authBearerToken", "authUsername", "authPassword", "authDomain", "authHost"))
        if signs_in:
            report.problem(at, "signs in with %s: those options send nothing until the system asks the HTTP way (a 401 with "
                               "WWW-Authenticate), and many systems with an API key never ask, so requests go out signed in as nobody. Set the header yourself: setHeader Authorization "
                               "with simple \"Bearer {{%s.api-key}}\" (connection_contract, authentication)" % (", ".join(signs_in), name))
        if uri.startswith("direct:agentricos.events.publish"):
            publishes.append(at)
        component = components.get(scheme) or next((c for c in components.values() if scheme in _alternatives(schema, c["name"])), None)
        if component is None:
            if scheme not in carried:
                report.warn(at, "“%s” is not among what the connections' program carries: it is fetched once when the connection is first started, which needs the network, and its options are not checked here" % scheme)
            return
        details = schema.file("camel/components/%s.json" % component["name"])
        properties = details.get("properties") or {}
        path = uri.split(":", 1)[1] if ":" in uri else ""
        path = path.split("?", 1)[0].lstrip("/")
        required_paths = [k for k, p in properties.items() if p.get("kind") == "path" and p.get("required")]
        if required_paths and not path:
            report.problem(at, "“%s” needs %s after “%s:”" % (uri, " and ".join(required_paths), scheme))
        prefixes = [p["prefix"] for p in properties.values() if p.get("prefix")]
        for key, value in (parameters or {}).items():
            if any(str(key).startswith(prefix) for prefix in prefixes):
                continue
            prop = properties.get(key)
            if prop is None or prop.get("kind") == "path":
                if details.get("lenientProperties"):
                    report.warn(at, "“%s” is not an option of %s: it is passed on to the system as it is" % (key, scheme))
                else:
                    near = [k for k in properties if k.lower().startswith(str(key)[:3].lower())][:6]
                    report.problem(at, "“%s” is not an option of %s%s" % (key, scheme, (": perhaps " + ", ".join(near)) if near else ""))
                continue
            literal = isinstance(value, str) and not PLACE.search(value) and "${" not in value
            if prop.get("enum") and literal and value not in prop["enum"]:
                report.problem(at, "“%s” of %s is “%s”; it can be one of: %s" % (key, scheme, value, ", ".join(prop["enum"])))
            if prop.get("secret") and value not in (None, "") and not (isinstance(value, str) and PLACE.search(value)):
                report.problem(at, "“%s” of %s is a secret: name a setting ({{%s.%s}}) instead of writing it in" % (key, scheme, name, key.lower()))

    def step(item, at, guarded=False):
        if not isinstance(item, dict) or len(item) != 1:
            return
        kind, body = next(iter(item.items()))
        here = "%s.%s" % (at, kind)
        if kind in ("process", "script"):
            report.problem(here, "Routes carry no code: say it with simple, jq and the EIPs instead")
        if isinstance(body, dict):
            if kind not in ("when", "otherwise", "doCatch", "doFinally") and not body.get("id"):
                report.warn(here, "has no id: give it one that says what it does")
            elif body.get("id"):
                ids.append(body["id"])
            for language in SCRIPTS & set(body):
                report.problem(here, "is written in %s: routes carry no code, write it in simple or jq" % language)
            if isinstance(body.get("expression"), dict):
                for language in SCRIPTS & set(body["expression"]):
                    report.problem(here, "is written in %s: routes carry no code, write it in simple or jq" % language)
            if kind in ENDPOINT_STEPS:
                endpoint(body.get("uri"), body.get("parameters"), here, kind in ("toD", "poll") or "${" in str(body.get("uri")))
            if kind == "bean":
                bean = body.get("ref") or body.get("beanType")
                if bean and bean not in helpers:
                    report.warn(here, "uses “%s”, a helper of a connection the platform comes with; a new connection does not build on it" % bean)
            if kind == "throwException":
                if body.get("exceptionType") not in refusals and not guarded:
                    report.problem(here, "A route refuses with one of: %s" % ", ".join(sorted(refusals)))
                if not body.get("message"):
                    report.problem(here, "says no message: whoever asked reads it, so say what is wrong and what to do")
            if kind == "setProperty" and body.get("name") == "eventSource":
                value = body.get("constant") or body.get("simple") or (body.get("expression") or {}).get("constant")
                if value and value not in pushed:
                    report.problem(here, "sends events of the source “%s”, which the description does not list without an operation" % value)
            for nested in NESTED:
                inner = body.get(nested)
                if isinstance(inner, list):
                    for i, child in enumerate(inner):
                        if nested == "when" or nested == "doCatch":
                            branch = "%s.%s[%d]" % (here, nested, i)
                            for j, s in enumerate((child or {}).get("steps") or []):
                                step(s, "%s.steps[%d]" % (branch, j), guarded)
                        else:
                            step(child, "%s.%s[%d]" % (here, nested, i), guarded or (kind == "doTry" and nested == "steps"))
                elif isinstance(inner, dict):
                    for j, s in enumerate(inner.get("steps") or []):
                        step(s, "%s.%s.steps[%d]" % (here, nested, j), guarded)
        elif isinstance(body, str) and kind in ENDPOINT_STEPS:
            endpoint(body, None, here, kind == "toD")

    for i, item in enumerate(routes):
        if not isinstance(item, dict):
            continue
        if "route" not in item:
            if set(item) - {"routeConfiguration", "beans"}:
                report.problem("routes[%d]" % i, "Each route starts with route: (with an id and from:)")
            continue
        route = item.get("route") or {}
        at = "routes[%d]" % i
        if not route.get("id"):
            report.problem(at, "The route has no id: give it one that says what it does")
        else:
            ids.append(route["id"])
        start = route.get("from") or {}
        uri = start.get("uri") if isinstance(start, dict) else None
        if isinstance(uri, str):
            address = uri.split("?", 1)[0]
            taking = address + "|" + str(sorted((start.get("parameters") or {}).items()))
            if taking in answered:
                report.problem(at, "Two routes start at %s: keep one" % address)
            answered[taking] = at
            answered.setdefault(address, at)
            endpoint(uri, start.get("parameters"), at + ".from", False)
            if address.startswith("direct:%s." % name) and address[len("direct:%s." % name):] not in promised:
                report.warn(at, "starts at %s, which the description does not promise: only other routes reach it" % address)
        for j, s in enumerate(start.get("steps") or [] if isinstance(start, dict) else []):
            step(s, "%s.steps[%d]" % (at, j))

    for operation in promised:
        if "direct:%s.%s" % (name, operation) not in answered:
            report.problem("routes", "The description promises “%s”, but no route carries it out. Add a route that starts with from: uri: direct:%s.%s" % (operation, name, operation))
    for i in set(x for x in ids if ids.count(x) > 1):
        report.problem("routes", "The id “%s” is used twice: ids say which step is which, keep each once" % i)
    if pushed and not publishes:
        report.problem("routes", "The source(s) %s send events themselves, but no route sends any to direct:agentricos.events.publish" % ", ".join(map(str, pushed)))

    for at, text in _texts(routes, "routes"):
        for place in PLACE.finditer(text):
            key = place.group(2)
            if key not in settings and key not in optional:
                report.problem(at, "names the setting “%s”, which the description lists neither under configuration nor under optionalConfiguration" % key)
            elif key in optional and not place.group(1) and not place.group(3):
                report.problem(at, "names “%s”, which the connection can do without, with no default: write {{%s:default}}, or {{?%s}} for an option" % (key, key, key))
    for key in settings | optional:
        if not any(re.search(r"\{\{\??%s(:[^}]*)?\}\}" % re.escape(key), text) for _, text in _texts(routes, "")):
            report.warn("description", "The setting “%s” is listed, but no route uses it" % key)


def _texts(value, at):
    if isinstance(value, str):
        yield at, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _texts(v, "%s.%s" % (at, k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _texts(v, "%s[%d]" % (at, i))


def _alternatives(schema, name):
    try:
        return (schema.file("camel/components/%s.json" % name).get("alternativeSchemes") or "").split(",")
    except LookupError:
        return []
