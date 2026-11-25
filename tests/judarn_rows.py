"""The form matrix: one throwaway repository per row. Each row states what `read` must find and what a roll-out dry run must say.

Generic names only: this repository is public.
"""
import json

GH = "https://github.com/acme/judarn"
OTHER_HOST = "https://git.example.test/acme/judarn"
SSH = "git@github.com:acme/judarn.git"
SHA = "a" * 40
V = "1.0.0"

SWIFT_FILES = ("Package.swift", "project.yml", "App.xcodeproj/project.xcproj", "App.xcodeproj/project.pbxproj")
SWIFT_REQS = ("exact", "range", "upToNextMinor", "upToNextMajor", "branch", "revision", "local", "unknown")
# What `read` reports as the requirement version for each type.
REQ_VERSION = {"exact": V, "range": V, "upToNextMinor": V, "upToNextMajor": V, "branch": "main", "revision": "abc123",
               "local": None, "unknown": None}
# Every (file name, requirement type) pair the reader can emit; "none" is a lock or resolved file without a manifest.
EMITTABLE = {(f.split("/")[-1], r) for f in SWIFT_FILES for r in SWIFT_REQS}
EMITTABLE |= {("package.json", r) for r in ("exact", "range", "local", "unknown")}
EMITTABLE |= {("package-lock.json", "none"), ("Package.resolved", "none")}


def swift_text(file, req, url=GH):
    name = file.split("/")[-1]
    if name == "Package.swift":
        body = {"exact": 'exact: "1.0.0"', "range": '"1.0.0"..<"2.0.0"', "upToNextMinor": '.upToNextMinor(from: "1.0.0")',
                "upToNextMajor": 'from: "1.0.0"', "branch": 'branch: "main"', "revision": 'revision: "abc123"', "unknown": ""}
        if req == "local":
            return '// swift-tools-version: 6.0\nlet deps: [Package.Dependency] = [\n  .package(path: "../packages/judarn"),\n]\n'
        tail = (", " + body[req]) if body[req] else ""
        return '// swift-tools-version: 6.0\nlet deps: [Package.Dependency] = [\n  .package(url: "%s"%s),\n]\n' % (url, tail)
    if name == "project.yml":
        if req == "local":
            return "packages:\n  Judarn:\n    path: ../packages/judarn\n"
        body = {"exact": "exactVersion: 1.0.0", "range": "minVersion: 1.0.0\n    maxVersion: 2.0.0", "upToNextMinor": "minorVersion: 1.0.0",
                "upToNextMajor": "majorVersion: 1.0.0", "branch": "branch: main", "revision": "revision: abc123", "unknown": ""}
        return "packages:\n  Judarn:\n    url: %s\n%s" % (url, ("    " + body[req] + "\n") if body[req] else "")
    if name == "project.xcproj":
        if req == "local":
            return '{\n  "packages": [\n    {\n      "kind": "local",\n      "path": "../packages/judarn",\n    },\n  ],\n}\n'
        body = {"exact": '"version-range-min": "1.0.0", "version-range-max": "1.0.1",', "range": '"version-range-min": "1.0.0", "version-range-max": "2.0.0",',
                "upToNextMinor": '"up-to-next-minor-version": "1.0.0",', "upToNextMajor": '"up-to-next-major-version": "1.0.0",',
                "branch": '"branch": "main",', "revision": '"revision": "abc123",', "unknown": ""}
        return ('{\n  "packages": [\n    {\n      "kind": "remote",\n      "repository": "%s",\n      "version": {\n        %s\n      },\n    },\n  ],\n}\n'
                % (url, body[req]))
    if req == "local":  # project.pbxproj
        return "\t\tAB12 = {\n\t\t\tisa = XCLocalSwiftPackageReference;\n\t\t\trelativePath = ../packages/judarn;\n\t\t};\n"
    body = {"exact": "kind = exactVersion;\n\t\t\t\tversion = 1.0.0;", "range": "kind = versionRange;\n\t\t\t\tminimumVersion = 1.0.0;\n\t\t\t\tmaximumVersion = 2.0.0;",
            "upToNextMinor": "kind = upToNextMinorVersion;\n\t\t\t\tminimumVersion = 1.0.0;",
            "upToNextMajor": "kind = upToNextMajorVersion;\n\t\t\t\tminimumVersion = 1.0.0;", "branch": "kind = branch;\n\t\t\t\tbranch = main;",
            "revision": "kind = revision;\n\t\t\t\trevision = abc123;", "unknown": ""}
    return ('\t\tAB12 = {\n\t\t\tisa = XCRemoteSwiftPackageReference;\n\t\t\trepositoryURL = "%s";\n\t\t\trequirement = {\n\t\t\t\t%s\n\t\t\t};\n\t\t};\n'
            % (url, body[req]))


def package_json(spec, group="dependencies"):
    return json.dumps({"name": "app", group: {"@judarn/ui": spec, "other": "1.2.3"}}, indent=2) + "\n"


def lock(version, style="v3"):
    if style == "v1":
        return json.dumps({"lockfileVersion": 1, "dependencies": {"@judarn/ui": {"version": version}}}) + "\n"
    return json.dumps({"lockfileVersion": 3, "packages": {"": {}, "node_modules/@judarn/ui": {"version": version}}}) + "\n"


def resolved(version, style="v3", ident="judarn", location=GH):
    if style == "v1":
        return json.dumps({"object": {"pins": [{"package": "Judarn", "repositoryURL": location,
                                                "state": {"branch": None, "revision": SHA, "version": version}}]}, "version": 1}) + "\n"
    return json.dumps({"pins": [{"identity": ident, "kind": "remoteSourceControl", "location": location,
                                 "state": {"revision": SHA, "version": version}}], "version": 3 if style == "v3" else 2}) + "\n"


XC_RESOLVED = "App.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved"
WS_RESOLVED = "App.xcworkspace/xcshareddata/swiftpm/Package.resolved"


def row(name, files, forms, rollout, version=None, problems=None, note=None, git=True):
    """forms: (kind, file, requirement type or "none", requirement version, resolved version, editable)."""
    return {"name": name, "files": files, "forms": forms, "rollout": rollout, "version": version, "problems": problems,
            "note": note, "git": git}


def rows():
    out = []
    # Every file kind against every requirement type, at the top level, with a GitHub HTTPS URL.
    for file in SWIFT_FILES:
        for req in SWIFT_REQS:
            editable = req == "exact" and not file.endswith("project.yml")
            if req == "local":
                status = "LOCAL"
            else:
                status = "WOULD UPDATE" if editable else "MANUAL"
            out.append(row("%s-%s" % (file.replace("/", "_"), req), {file: swift_text(file, req)},
                           [("swift", file, req, REQ_VERSION[req], None, editable)], status,
                           version=V if req == "exact" else None))
    # The same exact reference one folder down is read but not edited.
    for file in ("Package.swift", "sub/Package.swift", "sub/App.xcodeproj/project.pbxproj"):
        if file == "Package.swift":
            continue
        out.append(row("nested-" + file.replace("/", "_"), {file: swift_text(file, "exact")},
                       [("swift", file, "exact", V, None, False)], "MANUAL", version=V))
    # Hosts and URL shapes.
    out.append(row("ssh-url", {"Package.swift": swift_text("Package.swift", "exact", SSH)}, [("swift", "Package.swift", "exact", V, None, False)], "MANUAL", version=V))
    out.append(row("other-host", {"Package.swift": swift_text("Package.swift", "exact", OTHER_HOST)}, [("swift", "Package.swift", "exact", V, None, False)], "MANUAL", version=V))
    out.append(row("git-suffix", {"Package.swift": swift_text("Package.swift", "exact", GH + ".git")}, [("swift", "Package.swift", "exact", V, None, True)], "WOULD UPDATE", version=V))
    out.append(row("xcproj-ssh", {"App.xcodeproj/project.xcproj": swift_text("App.xcodeproj/project.xcproj", "exact", SSH)},
                   [("swift", "App.xcodeproj/project.xcproj", "exact", V, None, False)], "MANUAL", version=V))
    out.append(row("similar-name", {"Package.swift": swift_text("Package.swift", "exact", "https://github.com/acme/judarn-tools")}, [], "BLOCKED"))
    # Package.resolved in every version and every pairing.
    swift_exact = swift_text("Package.swift", "exact")
    for style in ("v1", "v2", "v3"):
        # The editor rewrites version 2 and 3 files; a version 1 file is read, and the dry run blocks rather than guess.
        out.append(row("resolved-" + style, {"Package.swift": swift_exact, "Package.resolved": resolved(V, style)},
                       [("swift", "Package.swift", "exact", V, V, style != "v1")], "BLOCKED" if style == "v1" else "WOULD UPDATE",
                       version=V, problems=[]))
    out.append(row("resolved-by-location", {"Package.swift": swift_exact, "Package.resolved": resolved(V, ident="design-kit")},
                   [("swift", "Package.swift", "exact", V, V, True)], "WOULD UPDATE", version=V))
    out.append(row("resolved-differs-from-exact", {"Package.swift": swift_exact, "Package.resolved": resolved("0.9.0")},
                   [("swift", "Package.swift", "exact", V, "0.9.0", True)], "WOULD UPDATE", version="0.9.0",
                   problems=["resolved 0.9.0 differs from exact 1.0.0"]))
    out.append(row("resolved-differs-from-floor", {"Package.swift": swift_text("Package.swift", "upToNextMajor"), "Package.resolved": resolved("1.0.2")},
                   [("swift", "Package.swift", "upToNextMajor", V, "1.0.2", False)], "MANUAL", version="1.0.2",
                   problems=["resolved 1.0.2 differs from floor 1.0.0"]))
    out.append(row("resolved-only", {"Package.resolved": resolved(V)}, [("swift", "Package.resolved", "none", None, V, False)], "MANUAL", version=V))
    out.append(row("resolved-beside-project", {"App.xcodeproj/project.xcproj": swift_text("App.xcodeproj/project.xcproj", "exact"), XC_RESOLVED: resolved(V)},
                   [("swift", "App.xcodeproj/project.xcproj", "exact", V, V, True)], "WOULD UPDATE", version=V))
    out.append(row("resolved-workspace-copy", {"App.xcodeproj/project.xcproj": swift_text("App.xcodeproj/project.xcproj", "exact"), WS_RESOLVED: resolved(V)},
                   [("swift", "App.xcodeproj/project.xcproj", "exact", V, V, False)], "WOULD UPDATE", version=V,
                   note=WS_RESOLVED))
    out.append(row("resolved-beside-yml", {"project.yml": swift_text("project.yml", "exact"), "Package.resolved": resolved(V)},
                   [("swift", "project.yml", "exact", V, V, False)], "MANUAL", version=V))
    out.append(row("resolved-pairs-with-its-own-manifest",
                   {"one/Package.swift": swift_exact, "one/Package.resolved": resolved("1.0.0"),
                    "two/Package.swift": swift_exact.replace("1.0.0", "1.1.0"), "two/Package.resolved": resolved("1.1.0")},
                   [("swift", "one/Package.swift", "exact", "1.0.0", "1.0.0", False), ("swift", "two/Package.swift", "exact", "1.1.0", "1.1.0", False)],
                   "MANUAL", problems=["the forms disagree: 1.0.0, 1.1.0"]))
    out.append(row("current", {"Package.swift": swift_exact.replace("1.0.0", "1.1.0"), "Package.resolved": resolved("1.1.0")},
                   [("swift", "Package.swift", "exact", "1.1.0", "1.1.0", True)], "CURRENT", version="1.1.0"))
    out.append(row("current-pbxproj", {"App.xcodeproj/project.pbxproj": swift_text("App.xcodeproj/project.pbxproj", "exact").replace("1.0.0", "1.1.0")},
                   [("swift", "App.xcodeproj/project.pbxproj", "exact", "1.1.0", None, True)], "CURRENT", version="1.1.0"))
    # Two references in one file.
    two = '.package(url: "%s", exact: "1.0.0"),\n.package(url: "%s", exact: "1.0.0"),\n'
    out.append(row("two-github-references", {"Package.swift": two % (GH, GH + ".git")},
                   [("swift", "Package.swift", "exact", V, None, False)] * 2, "BLOCKED", version=V, problems=["has 2 Judarn references"]))
    out.append(row("github-and-ssh-references", {"Package.swift": two % (GH, SSH)},
                   [("swift", "Package.swift", "exact", V, None, False)] * 2, "BLOCKED", version=V, problems=["has 2 Judarn references"]))
    out.append(row("local-and-remote", {"Package.swift": swift_exact + '.package(path: "../packages/judarn"),\n'},
                   [("swift", "Package.swift", "exact", V, None, True), ("swift", "Package.swift", "local", None, None, False)],
                   "WOULD UPDATE", version=V, problems=["Package.swift reaches Judarn by a local path"]))
    out.append(row("unbalanced-manifest", {"Package.swift": '.package(url: "%s", exact: "1.0.0"\n' % GH},
                   [("swift", "Package.swift", "exact", V, None, False)], "MANUAL", version=V))
    # npm.
    exact_json = package_json("1.0.0")
    out.append(row("npm-exact-lock-v3", {"package.json": exact_json, "package-lock.json": lock(V)},
                   [("npm", "package.json", "exact", V, V, True)], "WOULD UPDATE", version=V, problems=[]))
    out.append(row("npm-exact-lock-v1", {"package.json": exact_json, "package-lock.json": lock(V, "v1")},
                   [("npm", "package.json", "exact", V, V, True)], "WOULD UPDATE", version=V))
    out.append(row("npm-exact-no-lock", {"package.json": exact_json}, [("npm", "package.json", "exact", V, None, True)], "WOULD UPDATE", version=V))
    out.append(row("npm-lock-differs", {"package.json": exact_json, "package-lock.json": lock("0.9.0")},
                   [("npm", "package.json", "exact", V, "0.9.0", True)], "WOULD UPDATE", version="0.9.0",
                   problems=["lockfile holds 0.9.0, package.json says 1.0.0"]))
    out.append(row("npm-range", {"package.json": package_json("^1.0.0")}, [("npm", "package.json", "range", "^1.0.0", None, False)], "MANUAL",
                   problems=['range "^1.0.0"']))
    for group in ("devDependencies", "optionalDependencies"):
        out.append(row("npm-" + group, {"package.json": package_json("1.0.0", group)}, [("npm", "package.json", "exact", V, None, True)], "WOULD UPDATE", version=V))
    out.append(row("npm-peer", {"package.json": package_json("1.0.0", "peerDependencies")}, [("npm", "package.json", "exact", V, None, False)], "MANUAL", version=V))
    for spec in ("file:../packages/judarn", "link:../packages/judarn", "workspace:*", "github:acme/judarn"):
        out.append(row("npm-local-" + spec.split(":")[0], {"package.json": package_json(spec)}, [("npm", "package.json", "local", None, None, False)], "LOCAL",
                       problems=["package.json reaches Judarn by a local path"]))
    out.append(row("npm-invalid-json", {"package.json": '{ "dependencies": { "@judarn/ui": '}, [("npm", "package.json", "unknown", None, None, False)], "MANUAL",
                   problems=["cannot be parsed"]))
    out.append(row("npm-lock-only", {"package-lock.json": lock(V)}, [("npm", "package-lock.json", "none", None, V, False)], "MANUAL", version=V))
    out.append(row("npm-nested", {"web/package.json": exact_json, "web/package-lock.json": lock(V)},
                   [("npm", "web/package.json", "exact", V, V, False)], "MANUAL", version=V))
    out.append(row("npm-and-swift", {"package.json": exact_json, "package-lock.json": lock(V), "Package.swift": swift_exact},
                   [("npm", "package.json", "exact", V, V, True), ("swift", "Package.swift", "exact", V, None, True)], "WOULD UPDATE", version=V, problems=[]))
    out.append(row("npm-and-swift-disagree", {"package.json": exact_json, "Package.swift": swift_exact.replace("1.0.0", "0.9.0")},
                   [("npm", "package.json", "exact", V, None, True), ("swift", "Package.swift", "exact", "0.9.0", None, True)], "WOULD UPDATE",
                   problems=["the forms disagree: 0.9.0, 1.0.0"]))
    # Where it looks.
    for folder in (".build", "build", "DerivedData", "node_modules", "scripts", "Pods", ".hidden"):
        out.append(row("skips-" + folder.lstrip(".") + ("-hidden" if folder.startswith(".") else ""), {folder + "/Package.swift": swift_text("Package.swift", "exact")}, [], "BLOCKED"))
    out.append(row("depth-two", {"a/b/Package.swift": swift_exact}, [("swift", "a/b/Package.swift", "exact", V, None, False)], "MANUAL", version=V))
    out.append(row("depth-three", {"a/b/c/Package.swift": swift_exact}, [], "BLOCKED"))
    out.append(row("ignored-copy", {".gitignore": "*.xcodeproj\n", "Package.swift": swift_exact,
                                    "App.xcodeproj/project.xcproj": swift_text("App.xcodeproj/project.xcproj", "exact").replace("1.0.0", "0.9.0").replace("1.0.1", "0.9.1")},
                   [("swift", "Package.swift", "exact", V, None, True)], "WOULD UPDATE", version=V))
    out.append(row("outside-git", {"Package.swift": swift_exact, "App.xcodeproj/project.xcproj": swift_text("App.xcodeproj/project.xcproj", "range")},
                   [("swift", "Package.swift", "exact", V, None, True), ("swift", "App.xcodeproj/project.xcproj", "range", V, None, False)],
                   "BLOCKED", git=False))
    return out
