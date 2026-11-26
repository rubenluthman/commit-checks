"""roll-out list rules: the packages filter and its two mismatch directions, gates by package, {pins}, a package's own clone."""
import json
import os
import sys

from judarn_rows import swift_text
from test_judarn_pins import JudarnBase
from test_rollout import read, write

GATE_PINS = (
    "import json, os, sys\n"
    "here = os.path.dirname(os.path.abspath(__file__))\n"
    "with open(sys.argv[1]) as fh:\n"
    "    data = fh.read()\n"
    "with open(os.path.join(here, 'seen-pins.json'), 'w') as fh:\n"
    "    fh.write(data)\n"
    "with open(os.path.join(here, 'seen-path.txt'), 'w') as fh:\n"
    "    fh.write(sys.argv[1])\n"
    "json.dump({c['name']: {'decision': 'go', 'reason': 'ok'} for c in json.loads(data)['consumers']}, sys.stdout)\n"
)
SWIFT = {"Package.swift": swift_text("Package.swift", "exact")}


class TestListRules(JudarnBase):
    CHECK = ""

    def test_only_entries_that_name_the_package_are_worked_on(self):
        self.make_consumer("alpha")  # commit-checks only
        self.app("web", SWIFT)
        proc = self.go("judarn", "v1.1.0")
        self.assertPasses(proc)
        self.assertIn("WOULD UPDATE   web:", proc.stdout)
        self.assertNotIn("alpha", proc.stdout)
        self.assertIn("1 listed repository does not name judarn; not touched.", proc.stdout)
        self.assertIn("1 consumer(s); 0 need attention.", proc.stdout)

    def test_listed_but_no_pin_found_is_blocked(self):
        self.app("web", {"README.md": "nothing\n"})
        proc = self.go("judarn", "v1.1.0")
        self.assertBlocks(proc)
        self.assertIn("BLOCKED        web: listed as loading judarn, but no pin found", proc.stdout)

    def test_pin_found_but_not_listed_is_blocked(self):
        root = self.app("web", SWIFT)
        lst = self.write_list(consumers=[{"name": "web", "path": root, "packages": ["commit-checks"]}])
        write(os.path.join(root, ".commit-checks.json"), "{}")
        proc = self.roll(None, list_path=lst, pins={"judarn": "v1.1.0"}, env={"JUDARN_PATH": self.pkg})
        self.assertBlocks(proc)
        self.assertIn("BLOCKED        web: loads judarn, but the list does not name it", proc.stdout)

    def test_pin_found_but_not_listed_is_blocked_even_when_the_entry_names_another_requested_package(self):
        root = self.make_consumer("both")
        write(os.path.join(root, "Package.swift"), SWIFT["Package.swift"])
        self.git_commit(root)
        proc = self.roll(None, pins={"commit-checks": "v1.1.0", "judarn": "v1.1.0"}, env={"JUDARN_PATH": self.pkg})
        self.assertBlocks(proc)
        self.assertIn("loads judarn, but the list does not name it", proc.stdout)

    def git_commit(self, root):
        from test_rollout import git
        git(root, "add", "Package.swift")
        git(root, "commit", "-q", "-m", "chore: add a manifest")

    def test_a_package_with_no_requested_name_and_no_pin_is_not_touched(self):
        self.make_consumer("alpha")
        self.make_consumer("beta", checks=False, vale="v0.3.4")
        proc = self.roll(None, pins={"commit-checks": "v1.1.0"})
        self.assertPasses(proc)
        self.assertIn("1 listed repository does not name commit-checks; not touched.", proc.stdout)

    def gate_list(self):
        write(os.path.join(self.tmp, "gatepins.py"), GATE_PINS)
        return self.write_list({"gates": {"judarn": [sys.executable, "gatepins.py", "{pins}"]}})

    def test_gates_are_by_package(self):
        self.make_consumer("alpha")
        self.app("web", SWIFT)
        lst = self.gate_list()
        self.assertPasses(self.roll("v1.1.0", list_path=lst))  # a commit-checks run never calls the judarn gate
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "seen-pins.json")))
        proc = self.roll(None, list_path=lst, pins={"judarn": "v1.1.0"}, env={"JUDARN_PATH": self.pkg})
        self.assertPasses(proc)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "seen-pins.json")))

    def test_a_run_with_a_gated_package_takes_exactly_one_pin(self):
        self.app("web", SWIFT)
        self.assertErrors(self.roll(None, list_path=self.gate_list(), pins={"judarn": "v1.1.0", "commit-checks": "v1.1.0"},
                                    env={"JUDARN_PATH": self.pkg}), "exactly one --pin")

    def test_pins_placeholder_is_the_report_for_what_roll_out_acts_on(self):
        self.make_consumer("alpha")
        self.app("web", SWIFT)
        self.app("empty", {"README.md": "x\n"})  # names judarn, no pin: acted on (and blocked), so it is in the report
        lst = self.gate_list()
        self.roll(None, list_path=lst, pins={"judarn": "v1.1.0"}, env={"JUDARN_PATH": self.pkg})
        seen = json.loads(read(os.path.join(self.tmp, "seen-pins.json")))
        self.assertEqual(seen["format"], 1)
        self.assertEqual(sorted(c["name"] for c in seen["consumers"]), ["empty", "web"])  # alpha does not name judarn
        web = [c for c in seen["consumers"] if c["name"] == "web"][0]["packages"]["judarn"]
        self.assertEqual(web["forms"][0]["file"], "Package.swift")
        self.assertEqual(list(seen["consumers"][0]["packages"]), ["judarn"])
        self.assertFalse(os.path.exists(read(os.path.join(self.tmp, "seen-path.txt"))))  # the temporary file is gone

    def test_the_old_gate_key_is_an_error_that_names_gates(self):
        self.app("web", SWIFT)
        lst = self.write_list({"gate": [sys.executable, "gatepins.py"]})
        proc = self.roll(None, list_path=lst, pins={"judarn": "v1.1.0"}, env={"JUDARN_PATH": self.pkg})
        self.assertErrors(proc, "gates")

    def test_a_package_clone_is_never_a_consumer_of_itself(self):
        self.make_consumer("alpha")
        own = {"name": "pkg-itself", "path": self.pkg, "packages": ["commit-checks"]}
        lst = self.write_list(consumers=[self.consumers[0], own])
        proc = self.roll("v1.1.0", list_path=lst)
        self.assertPasses(proc)
        self.assertNotIn("pkg-itself", proc.stdout)
        self.assertIn("1 listed repository does not name commit-checks; not touched.", proc.stdout)

    def test_list_entries_need_known_packages(self):
        root = self.app("web", SWIFT)
        for bad in ({"consumers": [{"name": "web", "path": root}]},
                    {"consumers": [{"name": "web", "path": root, "packages": "judarn"}]},
                    {"consumers": [{"name": "web", "path": root, "packages": ["nope"]}]},
                    {"consumers": [], "gates": {"nope": ["x"]}},
                    {"consumers": [], "gates": {"judarn": "x"}}):
            path = os.path.join(self.tmp, "bad.json")
            write(path, json.dumps(bad))
            self.assertErrors(self.roll("v1.1.0", list_path=path))

    def test_a_listed_entry_with_a_missing_folder_is_blocked(self):
        lst = self.write_list(consumers=[{"name": "gone", "path": os.path.join(self.tmp, "nowhere"), "packages": ["judarn"]}])
        proc = self.roll(None, list_path=lst, pins={"judarn": "v1.1.0"}, env={"JUDARN_PATH": self.pkg})
        self.assertIn("BLOCKED        gone: folder not found", proc.stdout)
