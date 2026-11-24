"""roll-out pin types for Judarn: npm, a Swift manifest, an Xcode project. Fixtures only; npm is a stub."""
import json
import os
import stat

from test_rollout import RollOutBase, git, read, write

NPM_STUB = """#!/bin/sh
echo "$@" >> "$NPM_LOG"
if [ "$1" = view ]; then
  case "$NPM_PUBLISHED" in *"${2##*@}"*) echo "${2##*@}"; exit 0;; esac
  exit 1
fi
for last; do :; done
v="${last##*@}"
printf '{\\n  "packages": {\\n    "node_modules/@judarn/ui": {\\n      "version": "%s"\\n    }\\n  }\\n}\\n' "$v" > package-lock.json
"""
PACKAGE_JSON = '{\n  "name": "app",\n  "dependencies": {\n    "@judarn/ui": "%s",\n    "other": "1.2.3"\n  }\n}\n'
LOCK = '{\n  "packages": {\n    "node_modules/@judarn/ui": {\n      "version": "%s"\n    }\n  }\n}\n'
MANIFEST = ('// swift-tools-version: 6.0\nimport PackageDescription\nlet package = Package(\n  name: "App",\n  dependencies: [\n'
            '    // pinned\n    .package(url: "https://github.com/acme/judarn", %s),\n'
            '    .package(url: "https://github.com/acme/other.git", exact: "1.0.0")\n  ]\n)\n')
RESOLVED = ('{\n  "originHash" : "abc",\n  "pins" : [\n    {\n      "identity" : "judarn",\n      "kind" : "remoteSourceControl",\n'
            '      "location" : "https://github.com/acme/judarn",\n      "state" : {\n        "revision" : "%s",\n        "version" : "%s"\n      }\n    },\n'
            '    {\n      "identity" : "other",\n      "kind" : "remoteSourceControl",\n      "location" : "https://github.com/acme/other.git",\n'
            '      "state" : {\n        "revision" : "1111111111111111111111111111111111111111",\n        "version" : "1.0.0"\n      }\n    }\n  ],\n  "version" : 3\n}\n')
XCPROJ = ('{\n  "packages": [\n    {\n      "kind": "remote",\n      "repository": "https://github.com/acme/other",\n      "version": {\n        "up-to-next-major-version": "1.4.0",\n      },\n'
          '    }, {\n      "kind": "remote",\n      "repository": "https://github.com/acme/judarn",\n      "version": {\n%s      },\n    }, {\n      "kind": "local",\n      "path": "Packages/Core",\n    },\n  ],\n}\n')
RANGE = '        "version-range-min": "%s",\n        "version-range-max": "%s",\n'
PBX = ('\t\tAB12 /* XCRemoteSwiftPackageReference "judarn" */ = {\n\t\t\tisa = XCRemoteSwiftPackageReference;\n\t\t\trepositoryURL = "https://github.com/acme/judarn";\n'
       '\t\t\trequirement = {\n\t\t\t\tkind = %s;\n\t\t\t\tversion = %s;\n\t\t\t};\n\t\t};\n')
XC_RESOLVED = "App.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved"


class JudarnBase(RollOutBase):
    def setUp(self):
        super().setUp()
        self.bin = os.path.join(self.tmp, "bin")
        write(os.path.join(self.bin, "npm"), NPM_STUB, 0o755)
        self.log = os.path.join(self.tmp, "npm.log")
        self.sha = {t: self.tag_commit(t) for t in ("v1.0.0", "v1.0.1", "v1.1.0")}

    def app(self, name, files):
        root = self.make_consumer(name, checks=False)
        for rel, text in files.items():
            write(os.path.join(root, rel), text)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "chore: add pins")
        git(root, "push", "-q", "origin", "main")
        return root

    def go(self, kind, tag, *flags, published="1.0.1 1.1.0"):
        env = {"JUDARN_PATH": self.pkg, "NPM_LOG": self.log, "NPM_PUBLISHED": published,
               "PATH": self.bin + os.pathsep + os.environ["PATH"]}
        return self.roll(None, *flags, pins={kind: tag}, env=env)


class TestNpmPin(JudarnBase):
    def files(self, v="1.0.0", lock=True):
        out = {"package.json": PACKAGE_JSON % v}
        if lock:
            out["package-lock.json"] = LOCK % v
        return out

    def test_moves_package_json_and_lets_npm_make_the_lock(self):
        root = self.app("web", self.files())
        proc = self.go("judarn-npm", "v1.1.0", "--apply")
        self.assertPasses(proc)
        self.assertEqual(read(os.path.join(root, "package.json")), PACKAGE_JSON % "1.1.0")
        self.assertIn("1.1.0", read(os.path.join(root, "package-lock.json")))
        self.assertEqual(git(root, "log", "-1", "--format=%s"), "build: pin @judarn/ui at 1.1.0")
        self.assertEqual(git(root, "status", "--porcelain"), "")
        self.assertIn("install --package-lock-only --save-exact --ignore-scripts @judarn/ui@1.1.0", read(self.log))

    def test_dry_run_edits_nothing_and_does_not_run_install(self):
        root = self.app("web", self.files())
        head = self.head(root)
        proc = self.go("judarn-npm", "v1.1.0")
        self.assertPasses(proc)
        self.assertIn("WOULD UPDATE", proc.stdout)
        self.assertUntouched(root, head)
        self.assertNotIn("install", read(self.log))

    def test_unpublished_version_edits_nothing(self):
        root = self.app("web", self.files())
        head = self.head(root)
        proc = self.go("judarn-npm", "v1.1.0", "--apply", published="1.0.1")
        self.assertErrors(proc, "not published on npm")
        self.assertUntouched(root, head)

    def test_current(self):
        root = self.app("web", self.files("1.1.0"))
        head = self.head(root)
        proc = self.go("judarn-npm", "v1.1.0", "--apply")
        self.assertPasses(proc)
        self.assertIn("CURRENT", proc.stdout)
        self.assertUntouched(root, head)

    def test_range_is_noted_never_edited(self):
        root = self.app("web", {"package.json": PACKAGE_JSON % "^1.0.0"})
        head = self.head(root)
        proc = self.go("judarn-npm", "v1.1.0", "--apply")
        self.assertBlocks(proc)
        self.assertIn("MANUAL", proc.stdout)
        self.assertIn("not an exact version", proc.stdout)
        self.assertUntouched(root, head)

    def test_local_path_is_noted_never_edited(self):
        root = self.app("web", {"package.json": PACKAGE_JSON % "file:../judarn/platforms/web"})
        head = self.head(root)
        proc = self.go("judarn-npm", "v1.1.0", "--apply")
        self.assertPasses(proc)
        self.assertIn("LOCAL", proc.stdout)
        self.assertUntouched(root, head)

    def test_failing_npm_restores_files(self):
        root = self.app("web", self.files())
        head = self.head(root)
        write(os.path.join(self.bin, "npm"), '#!/bin/sh\nif [ "$1" = view ]; then echo 1.1.0; exit 0; fi\necho boom >&2\nexit 1\n', 0o755)
        proc = self.go("judarn-npm", "v1.1.0", "--apply")
        self.assertBlocks(proc)
        self.assertIn("BLOCKED", proc.stdout)
        self.assertUntouched(root, head)

    def test_no_package_json_entry_is_not_a_consumer(self):
        self.app("web", {"package.json": '{"dependencies": {"other": "1.0.0"}}\n'})
        self.assertIn("SKIPPED", self.go("judarn-npm", "v1.1.0").stdout)


class TestSwiftManifestPin(JudarnBase):
    def files(self, ref='exact: "1.0.0"', v="1.0.0"):
        return {"Package.swift": MANIFEST % ref, "Package.resolved": RESOLVED % (self.sha["v" + v], v)}

    def test_moves_exact_and_resolved(self):
        root = self.app("swiftapp", self.files())
        proc = self.go("judarn-swift", "v1.1.0", "--apply")
        self.assertPasses(proc)
        self.assertEqual(read(os.path.join(root, "Package.swift")), MANIFEST % 'exact: "1.1.0"')
        self.assertEqual(read(os.path.join(root, "Package.resolved")), RESOLVED % (self.sha["v1.1.0"], "1.1.0"))
        self.assertEqual(git(root, "log", "-1", "--format=%s"), "build: pin Judarn at v1.1.0")
        self.assertEqual(sorted(git(root, "show", "--name-only", "--format=", "HEAD").splitlines()), ["Package.resolved", "Package.swift"])

    def test_dot_exact_form(self):
        root = self.app("swiftapp", self.files('.exact("1.0.0")'))
        self.assertPasses(self.go("judarn-swift", "v1.0.1", "--apply"))
        self.assertIn('.exact("1.0.1")', read(os.path.join(root, "Package.swift")))

    def test_other_packages_untouched(self):
        root = self.app("swiftapp", self.files())
        self.go("judarn-swift", "v1.1.0", "--apply")
        self.assertIn('other.git", exact: "1.0.0"', read(os.path.join(root, "Package.swift")))
        self.assertIn('"revision" : "1111111111111111111111111111111111111111"', read(os.path.join(root, "Package.resolved")))

    def test_missing_resolved_is_noted(self):
        root = self.app("swiftapp", {"Package.swift": MANIFEST % 'exact: "1.0.0"'})
        proc = self.go("judarn-swift", "v1.1.0", "--apply")
        self.assertPasses(proc)
        self.assertIn("Package.resolved is missing", proc.stdout)
        self.assertIn('"1.1.0"', read(os.path.join(root, "Package.swift")))

    def test_range_is_noted_never_edited(self):
        root = self.app("swiftapp", self.files('from: "1.0.0"'))
        head = self.head(root)
        proc = self.go("judarn-swift", "v1.1.0", "--apply")
        self.assertBlocks(proc)
        self.assertIn("MANUAL", proc.stdout)
        self.assertUntouched(root, head)

    def test_local_path_is_noted_never_edited(self):
        local = MANIFEST.replace('.package(url: "https://github.com/acme/judarn", %s)', '.package(path: "../../Packages/judarn")')
        root = self.app("swiftapp", {"Package.swift": local})
        head = self.head(root)
        proc = self.go("judarn-swift", "v1.1.0", "--apply")
        self.assertPasses(proc)
        self.assertIn("LOCAL", proc.stdout)
        self.assertUntouched(root, head)

    def test_current(self):
        root = self.app("swiftapp", self.files('exact: "1.1.0"', "1.1.0"))
        head = self.head(root)
        proc = self.go("judarn-swift", "v1.1.0", "--apply")
        self.assertPasses(proc)
        self.assertIn("CURRENT", proc.stdout)
        self.assertUntouched(root, head)

    def test_unbalanced_manifest_is_not_a_consumer(self):
        self.app("swiftapp", {"Package.swift": '.package(url: "https://github.com/acme/judarn", exact: "1.0.0"\n'})
        self.assertIn("SKIPPED", self.go("judarn-swift", "v1.1.0").stdout)


class TestXcodeProjectPin(JudarnBase):
    def files(self, ref=None, v="1.0.0"):
        ref = ref or RANGE % (v, "1.0.1")
        return {"App.xcodeproj/project.xcproj": XCPROJ % ref, XC_RESOLVED: RESOLVED % (self.sha["v" + v], v)}

    def test_moves_one_patch_range_and_resolved(self):
        root = self.app("xcodeapp", self.files())
        proc = self.go("judarn-xcode", "v1.1.0", "--apply")
        self.assertPasses(proc)
        self.assertEqual(read(os.path.join(root, "App.xcodeproj/project.xcproj")), XCPROJ % (RANGE % ("1.1.0", "1.1.1")))
        self.assertEqual(read(os.path.join(root, XC_RESOLVED)), RESOLVED % (self.sha["v1.1.0"], "1.1.0"))
        self.assertEqual(git(root, "log", "-1", "--format=%s"), "build: pin Judarn at v1.1.0")

    def test_wider_range_is_noted_never_edited(self):
        root = self.app("xcodeapp", self.files(RANGE % ("1.0.0", "1.2.0")))
        head = self.head(root)
        proc = self.go("judarn-xcode", "v1.1.0", "--apply")
        self.assertBlocks(proc)
        self.assertIn("MANUAL", proc.stdout)
        self.assertUntouched(root, head)

    def test_up_to_next_major_is_noted_never_edited(self):
        root = self.app("xcodeapp", self.files('        "up-to-next-major-version": "1.0.0",\n'))
        head = self.head(root)
        self.assertBlocks(self.go("judarn-xcode", "v1.1.0", "--apply"))
        self.assertUntouched(root, head)

    def test_classic_project_file(self):
        root = self.app("xcodeapp", {"App.xcodeproj/project.pbxproj": PBX % ("exactVersion", "1.0.0"),
                                  XC_RESOLVED: RESOLVED % (self.sha["v1.0.0"], "1.0.0")})
        self.assertPasses(self.go("judarn-xcode", "v1.1.0", "--apply"))
        self.assertEqual(read(os.path.join(root, "App.xcodeproj/project.pbxproj")), PBX % ("exactVersion", "1.1.0"))

    def test_classic_up_to_next_major_is_noted(self):
        root = self.app("xcodeapp", {"App.xcodeproj/project.pbxproj": PBX % ("upToNextMajorVersion", "1.0.0")})
        head = self.head(root)
        self.assertBlocks(self.go("judarn-xcode", "v1.1.0", "--apply"))
        self.assertUntouched(root, head)

    def test_current(self):
        root = self.app("xcodeapp", self.files(v="1.1.0", ref=RANGE % ("1.1.0", "1.1.1")))
        head = self.head(root)
        proc = self.go("judarn-xcode", "v1.1.0", "--apply")
        self.assertPasses(proc)
        self.assertIn("CURRENT", proc.stdout)
        self.assertUntouched(root, head)

    def test_resolved_without_judarn_is_noted(self):
        files = self.files()
        files[XC_RESOLVED] = '{\n  "pins" : [],\n  "version" : 3\n}\n'
        root = self.app("xcodeapp", files)
        proc = self.go("judarn-xcode", "v1.1.0", "--apply")
        self.assertPasses(proc)
        self.assertIn("no judarn pin", proc.stdout)
        self.assertEqual(read(os.path.join(root, XC_RESOLVED)), files[XC_RESOLVED])
