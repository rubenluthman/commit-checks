"""The Judarn reader: a form matrix with one throwaway repository per row, read directly and through a roll-out dry run."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from judarn_rows import EMITTABLE, lock, package_json, resolved, rows, swift_text
from support import Base, env_for
from test_judarn_pins import JudarnBase
from test_rollout import git, write

from commit_checks.pins import PIN_TYPES


def summarize(reading):
    out = []
    for f in reading.public_forms():
        req, res = f["requirement"], f["resolved"]
        out.append((f["kind"], f["file"], req["type"] if req else "none", req["version"] if req else None,
                    res["version"] if res else None, f["editable"]))
    return sorted(out, key=repr)


class TestFormMatrix(Base):
    CHECK = ""

    def setUp(self):
        super().setUp()
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="cc-judarn-read-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make(self, row):
        root = os.path.join(self.tmp, row["name"])
        for rel, text in row["files"].items():
            write(os.path.join(root, rel), text)
        if row["git"]:
            git(root, "init", "-q", "-b", "main")
            git(root, "add", "-A")
        return root

    def test_every_row_reads_as_stated(self):
        for row in rows():
            with self.subTest(row["name"]):
                reading = PIN_TYPES["judarn"].read(self.make(row))
                self.assertEqual(summarize(reading), sorted(row["forms"], key=repr))
                if row["version"] is not None:
                    self.assertEqual(reading.version, row["version"])
                for wanted in row["problems"] or []:
                    self.assertTrue(any(wanted in p for p in reading.problems), "%r not in %r" % (wanted, reading.problems))
                if row["problems"] == []:
                    self.assertEqual(reading.problems, [])

    def test_the_table_holds_every_pair_the_reader_can_emit(self):
        seen = {(f[1].split("/")[-1], f[2]) for row in rows() for f in row["forms"]}
        self.assertEqual(sorted(EMITTABLE - seen), [], "rows missing for these (file, requirement) pairs")
        self.assertEqual(sorted(seen - EMITTABLE), [], "rows outside the list of pairs the reader can emit")

    def test_row_names_are_unique(self):
        names = [r["name"] for r in rows()]
        self.assertEqual(len(names), len(set(names)))

    def test_a_range_keeps_its_upper_bound(self):
        root = self.make([r for r in rows() if r["name"] == "App.xcodeproj_project.xcproj-range"][0])
        self.assertEqual(PIN_TYPES["judarn"].read(root).forms[0]["requirement"], {"type": "range", "version": "1.0.0", "max": "2.0.0"})

    def test_reader_keys_never_reach_a_report(self):
        root = self.make([r for r in rows() if r["name"] == "resolved-v3"][0])
        reading = PIN_TYPES["judarn"].read(root)
        self.assertTrue([k for f in reading.forms for k in f if k.startswith("_")])  # the reader does keep a key for itself
        self.assertFalse([k for f in reading.public_forms() for k in f if k.startswith("_")])


class TestFormMatrixRollOut(JudarnBase):
    """The same rows through a dry run: every one ends as stated, none is skipped or quietly current."""
    CHECK = ""

    def test_every_row_ends_as_stated(self):
        table = rows()
        for row in table:
            if row["git"]:
                self.app(row["name"], row["files"])
            else:
                root = os.path.join(self.tmp, "apps", row["name"])
                for rel, text in row["files"].items():
                    write(os.path.join(root, rel), text)
                self.consumers.append({"name": row["name"], "path": root, "packages": ["judarn"]})
        proc = self.go("judarn", "v1.1.0")
        blocks, name = {}, None
        for line in proc.stdout.splitlines():
            if line.startswith("    ") and name:
                blocks[name] += line + "\n"
            elif len(line) > 15 and line[:14].strip().isupper():
                name = line[15:].split(":")[0].strip()
                blocks[name] = line + "\n"
            else:
                name = None
        for row in table:
            with self.subTest(row["name"]):
                block = blocks.get(row["name"], "")
                self.assertTrue(block.startswith(row["rollout"] + " "), "%s: %r" % (row["name"], block))
                self.assertNotIn("SKIPPED", block)
                if row["rollout"] in ("MANUAL", "LOCAL"):
                    self.assertTrue(any(f[1] in block for f in row["forms"]), "no file named in: " + block)
                if row["note"]:
                    self.assertIn(row["note"], block)
                else:  # the editors cover these files; no part of them is left for a hand edit
                    if row["rollout"] in ("WOULD UPDATE", "CURRENT"):
                        self.assertNotIn("roll-out does not edit this file", block)


class TestPinsMode(JudarnBase):
    CHECK = ""

    def pins(self, *args, lst=None):
        e = env_for(extra={"COMMIT_CHECKS_PATH": self.pkg})
        return subprocess.run([sys.executable, "-m", "commit_checks", "pins", "--list", lst or self.write_list()] + list(args),
                              cwd=self.tmp, capture_output=True, text=True, env=e, timeout=120)

    def test_json_report_for_one_fixture_of_each_kind(self):
        self.app("web", {"package.json": package_json("1.0.0"), "package-lock.json": lock("1.0.0")})
        self.app("swiftapp", {"Package.swift": swift_text("Package.swift", "exact"), "Package.resolved": resolved("1.0.0")})
        self.app("xcodeapp", {"App.xcodeproj/project.xcproj": swift_text("App.xcodeproj/project.xcproj", "exact")})
        self.app("projectyml", {"project.yml": swift_text("project.yml", "upToNextMajor")})
        proc = self.pins("--package", "judarn", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["format"], 1)
        self.assertEqual(report["unlisted"], [])
        by = {c["name"]: c for c in report["consumers"]}
        self.assertEqual(sorted(by), ["projectyml", "swiftapp", "web", "xcodeapp"])
        web = by["web"]["packages"]["judarn"]
        self.assertEqual((web["declared"], web["detected"], web["version"], web["problems"]), (True, True, "1.0.0", []))
        self.assertEqual(web["forms"], [{"kind": "npm", "file": "package.json", "requirement": {"type": "exact", "version": "1.0.0"},
                                         "resolved": {"file": "package-lock.json", "version": "1.0.0", "revision": None, "branch": None}, "editable": True}])
        self.assertTrue(by["web"]["exists"])
        yml = by["projectyml"]["packages"]["judarn"]["forms"][0]
        self.assertEqual(yml["requirement"], {"type": "upToNextMajor", "version": "1.0.0"})
        self.assertFalse(yml["editable"])

    def test_the_report_is_complete_when_a_listed_package_is_missing_and_exits_zero(self):
        self.app("web", {"README.md": "nothing\n"})
        proc = self.pins("--package", "judarn", "--package", "commit-checks", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        pk = json.loads(proc.stdout)["consumers"][0]["packages"]
        self.assertEqual(pk["judarn"]["problems"], ["listed as loading judarn, but no pin found"])
        self.assertEqual((pk["commit-checks"]["declared"], pk["commit-checks"]["detected"]), (False, False))

    def test_pins_found_but_not_listed_are_a_problem(self):
        root = self.app("swiftapp", {"Package.swift": swift_text("Package.swift", "exact")})
        lst = self.write_list(consumers=[{"name": "swiftapp", "path": root, "packages": []}])
        pk = json.loads(self.pins("--package", "judarn", "--json", lst=lst).stdout)["consumers"][0]["packages"]["judarn"]
        self.assertEqual(pk["problems"], ["loads judarn, but the list does not name it"])

    def test_unlisted_repositories_are_reported(self):
        self.app("swiftapp", {"Package.swift": swift_text("Package.swift", "exact")})
        self.app("other", {"sub/Package.swift": swift_text("Package.swift", "exact")})
        lst = self.write_list({"scanRoot": "apps"}, consumers=[self.consumers[0]])
        report = json.loads(self.pins("--package", "judarn", "--json", lst=lst).stdout)
        self.assertEqual([(os.path.basename(u["path"]), u["loads"]) for u in report["unlisted"]], [("other", ["judarn"])])

    def test_a_missing_folder_is_data(self):
        lst = self.write_list(consumers=[{"name": "gone", "path": os.path.join(self.tmp, "nowhere"), "packages": ["judarn"]}])
        c = json.loads(self.pins("--package", "judarn", "--json", lst=lst).stdout)["consumers"][0]
        self.assertFalse(c["exists"])
        self.assertEqual(c["packages"]["judarn"]["problems"], ["folder not found"])

    def test_usage_errors_exit_two(self):
        self.app("web", {"package.json": "{}"})
        self.assertEqual(self.pins("--package", "nope").returncode, 2)
        self.assertEqual(self.pins().returncode, 2)
        bad = os.path.join(self.tmp, "bad.json")
        write(bad, "nope")
        self.assertEqual(self.pins("--package", "judarn", lst=bad).returncode, 2)

    def test_reads_the_other_package_types(self):
        self.make_consumer("alpha", vale="v0.3.4")
        proc = self.pins("--package", "commit-checks", "--package", "house-style", "--json")
        pk = json.loads(proc.stdout)["consumers"][0]["packages"]
        self.assertEqual(pk["commit-checks"]["version"], "v1.0.0")
        self.assertEqual(pk["house-style"]["version"], "v0.3.4")
        self.assertEqual(pk["house-style"]["forms"][0]["kind"], "vale")

    def test_text_output_lists_versions_and_problems(self):
        self.app("swiftapp", {"Package.swift": swift_text("Package.swift", "exact"), "Package.resolved": resolved("0.9.0")})
        proc = self.pins("--package", "judarn")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("swiftapp", proc.stdout)
        self.assertIn("PROBLEM resolved 0.9.0 differs from exact 1.0.0", proc.stdout)


if __name__ == "__main__":
    unittest.main()
