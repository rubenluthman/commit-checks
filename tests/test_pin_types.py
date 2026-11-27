"""roll-out and pins for the Swift library pin types (markdown-engine, apple-app-support, apple-test-support) and the shell pin type
(ai-output-evals), and the list keys they add. Fixtures only; consumer names are made up."""
import json
import os
import subprocess
import sys

from support import env_for
from test_rollout import RollOutBase, git, read, write

SWIFT_NAMES = ("markdown-engine", "apple-dev-support", "apple-app-support", "apple-test-support")
MANIFEST = ('// swift-tools-version: 6.0\nimport PackageDescription\nlet package = Package(\n  name: "App",\n  dependencies: [\n'
            '    // pinned\n    .package(url: "https://github.com/%(owner)s/%(name)s%(git)s", %(req)s),\n'
            '    .package(url: "https://github.com/acme/other.git", exact: "1.0.0")\n  ]\n)\n')
RESOLVED = ('{\n  "originHash" : "abc",\n  "pins" : [\n    {\n      "identity" : "%(name)s",\n      "kind" : "remoteSourceControl",\n'
            '      "location" : "https://github.com/acme/%(name)s",\n      "state" : {\n        "revision" : "%(rev)s",\n        "version" : "%(ver)s"\n      }\n    },\n'
            '    {\n      "identity" : "other",\n      "kind" : "remoteSourceControl",\n      "location" : "https://github.com/acme/other.git",\n'
            '      "state" : {\n        "revision" : "1111111111111111111111111111111111111111",\n        "version" : "1.0.0"\n      }\n    }\n  ],\n  "version" : 3\n}\n')
XCPROJ = ('{\n  "packages": [\n    {\n      "kind": "remote",\n      "repository": "https://github.com/acme/%(name)s",\n      "version": {\n%(req)s      },\n    }, {\n'
          '      "kind": "local",\n      "path": "Packages/Core",\n    },\n  ],\n}\n')
PBX = ('\t\tAB12 /* XCRemoteSwiftPackageReference "%(name)s" */ = {\n\t\t\tisa = XCRemoteSwiftPackageReference;\n\t\t\trepositoryURL = "https://github.com/acme/%(name)s";\n'
       '\t\t\trequirement = {\n\t\t\t\tkind = %(kind)s;\n\t\t\t\tversion = %(ver)s;\n\t\t\t};\n\t\t};\n')
XC_RESOLVED = "App.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved"
SHELL = '#!/bin/sh\nset -eu\n# the evals pin\nEVALS_TAG="%(tag)s"\nEVALS_COMMIT="%(commit)s"  # pinned\necho "run $EVALS_TAG"\n'


def manifest(name, req, owner="acme", git_suffix=""):
    return MANIFEST % {"name": name, "req": req, "owner": owner, "git": git_suffix}


class PinTypeBase(RollOutBase):
    def setUp(self):
        super().setUp()
        self.sha = {t: self.tag_commit(t) for t in ("v1.0.0", "v1.0.1", "v1.1.0")}

    def app(self, name, content, packages, **extra):
        while os.path.exists(os.path.join(self.tmp, "apps", name)):  # a subtest loop makes the same app again
            name += "x"
        root = self.make_consumer(name, checks=False, packages=packages)
        for rel, text in content.items():
            write(os.path.join(root, rel), text)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "chore: add pins")
        git(root, "push", "-q", "origin", "main")
        self.consumers[-1].update(extra)
        return root

    def go(self, package, tag, *flags):
        env = {package.upper().replace("-", "_") + "_PATH": self.pkg}
        return self.roll(None, *flags, pins={package: tag}, env=env)

    def pins(self, *packages, extra=None, as_json=False):
        argv = [sys.executable, "-m", "commit_checks", "pins", "--list", self.write_list(extra)]
        for p in packages:
            argv += ["--package", p]
        if as_json:
            argv.append("--json")
        return subprocess.run(argv, cwd=self.tmp, capture_output=True, text=True, env=env_for(), errors="replace")


class TestSwiftLibraryTypes(PinTypeBase):
    def test_manifest_exact_is_moved_with_its_resolved_file_for_each_package(self):
        for name in SWIFT_NAMES:
            with self.subTest(name=name):
                root = self.app("app-" + name, {
                    "Package.swift": manifest(name, 'exact: "1.0.0"'),
                    "Package.resolved": RESOLVED % {"name": name, "rev": self.sha["v1.0.0"], "ver": "1.0.0"}}, [name])
                proc = self.go(name, "v1.1.0", "--apply")
                self.assertPasses(proc)
                self.assertEqual(git(root, "log", "-1", "--format=%s"), "build: pin %s at v1.1.0" % name)
                self.assertEqual(read(os.path.join(root, "Package.swift")), manifest(name, 'exact: "1.1.0"'))
                self.assertEqual(read(os.path.join(root, "Package.resolved")), RESOLVED % {"name": name, "rev": self.sha["v1.1.0"], "ver": "1.1.0"})
                self.assertEqual(git(root, "status", "--porcelain"), "")

    def test_any_owner_and_a_dot_git_suffix_match_and_dot_exact_form(self):
        root = self.app("alpha", {"Package.swift": manifest("markdown-engine", '.exact("1.0.0")', owner="someone-else", git_suffix=".git")}, ["markdown-engine"])
        self.assertPasses(self.go("markdown-engine", "v1.0.1", "--apply"))
        self.assertEqual(read(os.path.join(root, "Package.swift")), manifest("markdown-engine", '.exact("1.0.1")', owner="someone-else", git_suffix=".git"))

    def test_another_package_with_a_longer_name_is_not_this_one(self):
        self.app("alpha", {"Package.swift": manifest("markdown-engine-extras", 'exact: "1.0.0"')}, ["markdown-engine"])
        proc = self.go("markdown-engine", "v1.1.0")
        self.assertBlocks(proc)
        self.assertIn("listed as loading markdown-engine, but no pin found", proc.stdout)

    def test_xcode_one_patch_range_and_its_resolved_file(self):
        rng = '        "version-range-min": "%s",\n        "version-range-max": "%s",\n'
        root = self.app("alpha", {
            "App.xcodeproj/project.xcproj": XCPROJ % {"name": "apple-app-support", "req": rng % ("1.0.0", "1.0.1")},
            XC_RESOLVED: RESOLVED % {"name": "apple-app-support", "rev": self.sha["v1.0.0"], "ver": "1.0.0"}}, ["apple-app-support"])
        self.assertPasses(self.go("apple-app-support", "v1.1.0", "--apply"))
        self.assertEqual(read(os.path.join(root, "App.xcodeproj/project.xcproj")), XCPROJ % {"name": "apple-app-support", "req": rng % ("1.1.0", "1.1.1")})
        self.assertEqual(read(os.path.join(root, XC_RESOLVED)), RESOLVED % {"name": "apple-app-support", "rev": self.sha["v1.1.0"], "ver": "1.1.0"})
        self.assertEqual(git(root, "log", "-1", "--format=%s"), "build: pin apple-app-support at v1.1.0")

    def test_xcode_exact_version_in_a_classic_project_file(self):
        root = self.app("alpha", {"App.xcodeproj/project.pbxproj": PBX % {"name": "apple-test-support", "kind": "exactVersion", "ver": "1.0.0"}}, ["apple-test-support"])
        proc = self.go("apple-test-support", "v1.1.0", "--apply")
        self.assertPasses(proc)
        self.assertEqual(read(os.path.join(root, "App.xcodeproj/project.pbxproj")), PBX % {"name": "apple-test-support", "kind": "exactVersion", "ver": "1.1.0"})
        self.assertIn("project.xcworkspace/xcshareddata/swiftpm/Package.resolved is missing; not created", proc.stdout)

    def test_a_range_is_manual_and_exit_1_without_library(self):
        for req in ('.upToNextMinor(from: "1.0.0")', 'from: "1.0.0"', '.upToNextMajor(from: "1.0.0")'):
            with self.subTest(req=req):
                root = self.app("lib", {"Package.swift": manifest("markdown-engine", req)}, ["markdown-engine"])
                head = git(root, "rev-parse", "HEAD")
                proc = self.go("markdown-engine", "v1.1.0", "--apply")
                self.assertBlocks(proc)
                self.assertIn("MANUAL", proc.stdout)
                self.assertEqual(git(root, "rev-parse", "HEAD"), head)
                self.consumers.clear()

    def test_a_range_under_library_is_range_and_exit_0(self):
        for req in ('.upToNextMinor(from: "1.0.0")', 'from: "1.0.0"', '.upToNextMajor(from: "1.0.0")'):
            with self.subTest(req=req):
                root = self.app("lib", {"Package.swift": manifest("markdown-engine", req)}, ["markdown-engine"], library=True)
                head = git(root, "rev-parse", "HEAD")
                proc = self.go("markdown-engine", "v1.1.0", "--apply")
                self.assertPasses(proc)
                self.assertIn("RANGE", proc.stdout)
                self.assertIn("Package.swift", proc.stdout)
                self.assertNotIn("MANUAL", proc.stdout)
                self.assertEqual(git(root, "rev-parse", "HEAD"), head)
                self.consumers.clear()

    def test_library_does_not_excuse_a_branch(self):
        self.app("lib", {"Package.swift": manifest("markdown-engine", 'branch: "main"')}, ["markdown-engine"], library=True)
        proc = self.go("markdown-engine", "v1.1.0")
        self.assertBlocks(proc)
        self.assertIn("MANUAL", proc.stdout)

    def test_a_library_range_in_an_xcode_project_is_range(self):
        pbx = PBX % {"name": "apple-test-support", "kind": "upToNextMinorVersion", "ver": "1.0.0"}
        self.app("lib", {"App.xcodeproj/project.pbxproj": pbx}, ["apple-test-support"], library=True)
        proc = self.go("apple-test-support", "v1.1.0")
        self.assertPasses(proc)
        self.assertIn("RANGE", proc.stdout)
        self.consumers.clear()
        self.app("lib2", {"App.xcodeproj/project.pbxproj": pbx}, ["apple-test-support"])
        self.assertBlocks(self.go("apple-test-support", "v1.1.0"))

    def test_a_local_path_is_local(self):
        manifest_text = ('// swift-tools-version: 6.0\nimport PackageDescription\nlet package = Package(\n  name: "App",\n  dependencies: [\n'
                         '    .package(path: "../markdown-engine")\n  ]\n)\n')
        self.app("alpha", {"Package.swift": manifest_text}, ["markdown-engine"])
        proc = self.go("markdown-engine", "v1.1.0")
        self.assertPasses(proc)
        self.assertIn("LOCAL", proc.stdout)

    def test_local_path_in_an_xcode_project_is_local(self):
        pbx = ('\t\tAB12 /* XCLocalSwiftPackageReference "../markdown-engine" */ = {\n\t\t\tisa = XCLocalSwiftPackageReference;\n'
               '\t\t\trelativePath = "../markdown-engine";\n\t\t};\n')
        self.app("alpha", {"App.xcodeproj/project.pbxproj": pbx}, ["markdown-engine"])
        proc = self.go("markdown-engine", "v1.1.0")
        self.assertPasses(proc)
        self.assertIn("LOCAL", proc.stdout)

    def test_pins_reads_each_swift_package_in_text_and_json(self):
        self.app("alpha", {"Package.swift": manifest("markdown-engine", 'exact: "1.0.0"'),
                           "Package.resolved": RESOLVED % {"name": "markdown-engine", "rev": self.sha["v1.0.0"], "ver": "1.0.0"}}, ["markdown-engine"])
        self.app("lib", {"Package.swift": manifest("apple-app-support", '.upToNextMinor(from: "1.0.0")')}, ["apple-app-support"], library=True)
        proc = self.pins("markdown-engine", "apple-app-support", "apple-test-support")
        self.assertPasses(proc)
        self.assertIn("markdown-engine", proc.stdout)
        self.assertIn("1.0.0", proc.stdout)
        data = json.loads(self.pins("markdown-engine", "apple-app-support", as_json=True).stdout)
        by = {c["name"]: c["packages"] for c in data["consumers"]}
        self.assertEqual(by["alpha"]["markdown-engine"]["version"], "1.0.0")
        self.assertTrue(by["alpha"]["markdown-engine"]["forms"][0]["editable"])
        self.assertEqual(by["lib"]["apple-app-support"]["forms"][0]["requirement"], {"type": "upToNextMinor", "version": "1.0.0"})
        self.assertFalse(by["lib"]["apple-app-support"]["forms"][0]["editable"])


class TestShellPin(PinTypeBase):
    def evals(self, text=None, tag="v1.0.0", **extra):
        text = text if text is not None else SHELL % {"tag": tag, "commit": self.sha[tag]}
        return self.app("evals-app", {"scripts/eval.sh": text}, ["ai-output-evals"], files={"ai-output-evals": "scripts/eval.sh"}, **extra)

    def test_edits_both_values_and_leaves_every_other_byte(self):
        root = self.evals()
        proc = self.go("ai-output-evals", "v1.1.0", "--apply")
        self.assertPasses(proc)
        self.assertEqual(read(os.path.join(root, "scripts/eval.sh")), SHELL % {"tag": "v1.1.0", "commit": self.sha["v1.1.0"]})
        self.assertEqual(git(root, "log", "-1", "--format=%s"), "build: pin ai-output-evals at v1.1.0")
        self.assertEqual(git(root, "show", "--name-only", "--format=", "HEAD"), "scripts/eval.sh")

    def test_crlf_line_endings_survive(self):
        text = (SHELL % {"tag": "v1.0.0", "commit": self.sha["v1.0.0"]}).replace("\n", "\r\n")
        root = self.evals(text)
        self.assertPasses(self.go("ai-output-evals", "v1.1.0", "--apply"))
        with open(os.path.join(root, "scripts/eval.sh"), "rb") as fh:
            self.assertEqual(fh.read(), (SHELL % {"tag": "v1.1.0", "commit": self.sha["v1.1.0"]}).replace("\n", "\r\n").encode())

    def test_already_current(self):
        root = self.evals(tag="v1.1.0")
        head = git(root, "rev-parse", "HEAD")
        proc = self.go("ai-output-evals", "v1.1.0", "--apply")
        self.assertPasses(proc)
        self.assertIn("CURRENT", proc.stdout)
        self.assertEqual(git(root, "rev-parse", "HEAD"), head)

    def test_a_wrong_commit_with_the_right_tag_is_moved(self):
        root = self.evals(SHELL % {"tag": "v1.1.0", "commit": "0" * 40})
        self.assertPasses(self.go("ai-output-evals", "v1.1.0", "--apply"))
        self.assertIn(self.sha["v1.1.0"], read(os.path.join(root, "scripts/eval.sh")))

    def test_a_missing_doubled_or_malformed_line_is_refused_and_nothing_is_edited(self):
        good = SHELL % {"tag": "v1.0.0", "commit": self.sha["v1.0.0"]}
        cases = {
            "no tag line": good.replace('EVALS_TAG="v1.0.0"\n', ""),
            "no commit line": good.replace('EVALS_COMMIT="%s"  # pinned\n' % self.sha["v1.0.0"], ""),
            "tag twice": good + 'EVALS_TAG="v1.0.1"\n',
            "commit twice": good + 'EVALS_COMMIT="%s"\n' % self.sha["v1.0.1"],
            "tag without quotes": good.replace('EVALS_TAG="v1.0.0"', "EVALS_TAG=v1.0.0"),
            "tag not a release": good.replace('"v1.0.0"', '"main"'),
            "short commit": good.replace(self.sha["v1.0.0"], self.sha["v1.0.0"][:12]),
            "tag spelled by a variable": good.replace('EVALS_TAG="v1.0.0"', 'EVALS_TAG="$X"'),
        }
        for label, text in cases.items():
            with self.subTest(label):
                root = self.evals(text)
                head = git(root, "rev-parse", "HEAD")
                proc = self.go("ai-output-evals", "v1.1.0", "--apply")
                self.assertBlocks(proc)
                self.assertIn("BLOCKED", proc.stdout)
                self.assertEqual(git(root, "rev-parse", "HEAD"), head)
                self.assertEqual(git(root, "status", "--porcelain"), "")
                self.consumers.clear()

    def test_an_entry_without_a_files_path_is_blocked(self):
        self.app("evals-app", {"scripts/eval.sh": SHELL % {"tag": "v1.0.0", "commit": self.sha["v1.0.0"]}}, ["ai-output-evals"])
        proc = self.go("ai-output-evals", "v1.1.0")
        self.assertBlocks(proc)
        self.assertIn("no pin found", proc.stdout)

    def test_pins_reads_the_shell_pin_in_text_and_json(self):
        self.evals()
        self.assertIn("v1.0.0", self.pins("ai-output-evals").stdout)
        data = json.loads(self.pins("ai-output-evals", as_json=True).stdout)
        pk = data["consumers"][0]["packages"]["ai-output-evals"]
        self.assertEqual(pk["version"], "v1.0.0")
        self.assertEqual(pk["forms"][0]["kind"], "shell")
        self.assertEqual(pk["forms"][0]["file"], "scripts/eval.sh")
        self.assertEqual(pk["forms"][0]["resolved"]["revision"], self.sha["v1.0.0"])

    def test_pins_reports_a_malformed_shell_pin_as_a_problem(self):
        self.evals(SHELL % {"tag": "v1.0.0", "commit": self.sha["v1.0.0"]} + 'EVALS_TAG="v1.0.1"\n')
        out = self.pins("ai-output-evals").stdout
        self.assertIn("EVALS_TAG is set 2 times", out)


class TestListKeys(PinTypeBase):
    def check(self, mutate, text=None, ok=False):
        self.consumers.clear()
        root = self.app("alpha", {"Package.swift": manifest("markdown-engine", 'exact: "1.0.0"')}, ["markdown-engine"])
        entry = self.consumers[-1]
        extra = {}
        mutate(entry, extra)
        proc = self.pins("markdown-engine", extra=extra)
        if ok:
            self.assertPasses(proc)
        else:
            self.assertErrors(proc, text)
        return root

    def test_checks(self):
        self.check(lambda e, x: e.update(checks={"markdown-engine": ["./scripts/quick.sh", "arg"]}), ok=True)
        self.check(lambda e, x: e.update(checks={"markdown-engine": []}), "checks command")
        self.check(lambda e, x: e.update(checks={"markdown-engine": "make"}), "checks command")
        self.check(lambda e, x: e.update(checks={"markdown-engine": ["ok", 3]}), "checks command")
        self.check(lambda e, x: e.update(checks={"nope": ["x"]}), "unknown package")

    def test_files(self):
        self.check(lambda e, x: e.update(files={"ai-output-evals": "scripts/eval.sh"}), ok=True)
        self.check(lambda e, x: e.update(files={"nope": "a.sh"}), "unknown package")
        self.check(lambda e, x: e.update(files={"ai-output-evals": "/etc/eval.sh"}), "relative path")
        self.check(lambda e, x: e.update(files={"ai-output-evals": "../eval.sh"}), "relative path")
        self.check(lambda e, x: e.update(files={"ai-output-evals": "a/../../eval.sh"}), "relative path")
        self.check(lambda e, x: e.update(files={"ai-output-evals": ""}), "relative path")
        self.check(lambda e, x: e.update(files={"ai-output-evals": 4}), "relative path")
        self.check(lambda e, x: e.update(files=["scripts/eval.sh"]), "files must be an object")

    def test_library(self):
        self.check(lambda e, x: e.update(library=True), ok=True)
        self.check(lambda e, x: e.update(library="yes"), "library must be true or false")

    def test_scan_skip(self):
        os.makedirs(os.path.join(self.tmp, "repos"))
        self.check(lambda e, x: x.update(scanRoot="repos", scanSkip=["private"]), ok=True)
        self.check(lambda e, x: x.update(scanRoot="repos", scanSkip="private"), "scanSkip")
        self.check(lambda e, x: x.update(scanRoot="repos", scanSkip=[3]), "scanSkip")
        self.check(lambda e, x: x.update(scanRoot="repos", scanSkip=["/abs"]), "scanSkip")
        self.check(lambda e, x: x.update(scanRoot="repos", scanSkip=["../elsewhere"]), "scanSkip")
        self.check(lambda e, x: x.update(scanRoot="repos", scanSkip=["a/../../x"]), "scanSkip")
        self.check(lambda e, x: x.update(scanRoot="repos", scanSkip=[""]), "scanSkip")
        self.check(lambda e, x: x.update(scanSkip=["private"]), "scanSkip needs a scanRoot")

    def test_scan_skip_hides_a_repository_from_the_unlisted_scan(self):
        for folder in ("open", "private"):
            repo = os.path.join(self.tmp, "repos", folder, "hidden-app")
            os.makedirs(repo)
            git(repo, "init", "-q", "-b", "main")
            write(os.path.join(repo, "Package.swift"), manifest("markdown-engine", 'exact: "1.0.0"'))
            git(repo, "add", "-A")
            git(repo, "-c", "user.email=1+t@users.noreply.github.com", "-c", "user.name=T", "commit", "-q", "-m", "chore: start")
        self.app("alpha", {"Package.swift": manifest("markdown-engine", 'exact: "1.0.0"')}, ["markdown-engine"])
        both = self.pins("markdown-engine", extra={"scanRoot": "repos"}).stdout
        self.assertIn("UNLISTED", both)
        self.assertIn(os.path.join("private", "hidden-app"), both)
        skipped = self.pins("markdown-engine", extra={"scanRoot": "repos", "scanSkip": ["private"]}).stdout
        self.assertIn(os.path.join("open", "hidden-app"), skipped)
        self.assertNotIn(os.path.join("private", "hidden-app"), skipped)
        # roll-out honors the same list: the skipped repository does not make the run exit 1
        argv = [sys.executable, "-m", "commit_checks", "roll-out", "--list", self.write_list({"scanRoot": "repos", "scanSkip": ["private", "open"]}),
                "--pin", "markdown-engine=v1.0.0"]
        proc = subprocess.run(argv, cwd=self.tmp, capture_output=True, text=True, errors="replace",
                              env=env_for(extra={"MARKDOWN_ENGINE_PATH": self.pkg}))
        self.assertPasses(proc)
        self.assertNotIn("UNLISTED", proc.stdout)
