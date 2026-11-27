"""roll-out flow: --only, `latest`, `commit-checks verify` first (WAITS), the full script only when needed,
per-consumer `checks`, and several gated packages in one run."""
import json
import os
import sys

from test_judarn_pins import JudarnBase
from judarn_rows import swift_text
from test_rollout import LOADER_V1, VERIFY_FAIL, VERIFY_OK, git, pid_alive, read, wait_until, write

LOADER_BUMP = '#!/usr/bin/env python3\nimport sys\nprint("code changed under the released tag v1.0.0; bump the version")\nsys.exit(1)\n'
LOADER_BROKEN = '#!/usr/bin/env python3\nimport sys\nprint("a check exploded")\nsys.exit(1)\n'
SWIFT = {"Package.swift": swift_text("Package.swift", "exact")}


class FlowBase(JudarnBase):
    CHECK = ""

    def add_release(self, tag, loader, push=True):
        write(os.path.join(self.pkg, "templates/loader.py"), loader)
        git(self.pkg, "add", "templates/loader.py")
        git(self.pkg, "commit", "-q", "-m", "release " + tag)
        git(self.pkg, "tag", "-a", tag, "-m", tag)
        if push:
            git(self.pkg, "push", "-q", "origin", "main", "--tags")

    def both(self, name, verify=VERIFY_OK, checks=None):
        """A consumer that loads commit-checks (at v1.0.0) and judarn (exact in Package.swift)."""
        root = self.make_consumer(name, verify=verify, packages=["commit-checks", "judarn"])
        write(os.path.join(root, "Package.swift"), SWIFT["Package.swift"])
        git(root, "add", "Package.swift")
        git(root, "commit", "-q", "-m", "chore: add a manifest")
        git(root, "push", "-q", "origin", "main")
        if checks:
            self.consumers[-1]["checks"] = checks
        return root

    def judarn_roll(self, *flags, pins=None, list_path=None):
        return self.roll(None, *flags, pins=pins, list_path=list_path, env={"JUDARN_PATH": self.pkg})

    def verify_runs(self, root):
        path = os.path.join(root, ".git/verify-runs")
        return read(path).split() if os.path.exists(path) else []


class TestOnly(FlowBase):
    def test_only_acts_on_the_named_consumers_and_counts_the_rest(self):
        a = self.make_consumer("alpha")
        b = self.make_consumer("beta")
        head = self.head(b)
        proc = self.roll("v1.1.0", "--apply", "--only", "alpha")
        self.assertPasses(proc)
        self.assertEqual(self.settings(a)["version"], "v1.1.0")
        self.assertUntouched(b, head)
        self.assertNotIn("beta:", proc.stdout)
        self.assertIn("1 listed repository was not selected by --only; not touched.", proc.stdout)

    def test_only_is_repeatable(self):
        roots = [self.make_consumer(n) for n in ("alpha", "beta", "gamma")]
        self.assertPasses(self.roll("v1.1.0", "--apply", "--only", "alpha", "--only", "gamma"))
        self.assertEqual([self.settings(r)["version"] for r in roots], ["v1.1.0", "v1.0.0", "v1.1.0"])

    def test_an_unknown_name_is_an_error_and_edits_nothing(self):
        a = self.make_consumer("alpha")
        head = self.head(a)
        self.assertErrors(self.roll("v1.1.0", "--apply", "--only", "nowhere"), "--only names a consumer that is not listed")
        self.assertUntouched(a, head)


class TestLatest(FlowBase):
    def test_latest_is_the_highest_version_not_the_newest_tag(self):
        a = self.make_consumer("alpha")
        git(self.pkg, "tag", "-a", "v0.9.0", "-m", "old number, new date")
        git(self.pkg, "push", "-q", "origin", "--tags")
        self.assertPasses(self.roll(None, "--apply", pins={"commit-checks": "latest"}))
        self.assertEqual(self.settings(a)["version"], "v1.1.0")

    def test_latest_compares_numbers_not_text(self):
        a = self.make_consumer("alpha")
        self.add_release("v1.10.0", LOADER_V1)
        self.assertPasses(self.roll(None, "--apply", pins={"commit-checks": "latest"}))
        self.assertEqual(self.settings(a)["version"], "v1.10.0")

    def test_latest_with_no_tags_is_an_error(self):
        self.make_consumer("alpha", checks=False, vale="v0.3.4")
        bare = os.path.join(self.tmp, "untagged")
        os.makedirs(bare)
        git(bare, "init", "-q", "-b", "main")
        git(bare, "config", "user.email", "1+t@users.noreply.github.com")
        git(bare, "config", "user.name", "T")
        write(os.path.join(bare, "x.txt"), "x\n")
        git(bare, "add", "-A")
        git(bare, "commit", "-q", "-m", "one")
        proc = self.roll(None, "--apply", pins={"house-style": "latest"}, env={"HOUSE_STYLE_PATH": bare})
        self.assertErrors(proc, "no release tag")

    def test_latest_missing_on_origin_is_an_error_and_edits_nothing(self):
        a = self.make_consumer("alpha")
        head = self.head(a)
        self.add_release("v2.0.0", LOADER_V1, push=False)
        self.assertErrors(self.roll(None, "--apply", pins={"commit-checks": "latest"}), "not on the package's remote")
        self.assertUntouched(a, head)


class TestWaits(FlowBase):
    def test_a_version_bump_request_undoes_the_edits_and_waits(self):
        self.add_release("v1.2.0", LOADER_BUMP)
        a = self.make_consumer("alpha", verify=VERIFY_OK)
        head = self.head(a)
        proc = self.roll("v1.2.0", "--apply")
        self.assertPasses(proc)  # waiting is not a failure
        self.assertIn("WAITS          alpha: waits for its next version", proc.stdout)
        self.assertUntouched(a, head)
        self.assertEqual(self.settings(a)["version"], "v1.0.0")
        self.assertEqual(read(os.path.join(a, "scripts/commit-checks")), LOADER_V1)
        self.assertEqual(self.verify_runs(a), [])
        self.assertIn("0 need attention.", proc.stdout)

    def test_waits_leaves_the_exit_code_as_the_others_make_it(self):
        self.add_release("v1.2.0", LOADER_BUMP)
        self.make_consumer("alpha")
        b = self.make_consumer("beta")
        write(os.path.join(b, "scratch.txt"), "x\n")  # skipped: uncommitted, so the run exits 1 whatever alpha does
        proc = self.roll("v1.2.0", "--apply")
        self.assertBlocks(proc)
        self.assertIn("WAITS          alpha", proc.stdout)
        self.assertIn("need attention: beta.", proc.stdout)
        self.assertNotIn("alpha.", proc.stdout.splitlines()[-1])

    def test_another_failure_of_commit_checks_verify_is_checks_fail(self):
        self.add_release("v1.2.0", LOADER_BROKEN)
        a = self.make_consumer("alpha", verify=VERIFY_OK)
        head = self.head(a)
        logs = os.path.join(self.tmp, "logs")
        proc = self.roll("v1.2.0", "--apply", "--log-dir", logs)
        self.assertBlocks(proc)
        self.assertIn("CHECKS FAIL", proc.stdout)
        self.assertIn("a check exploded", proc.stdout)
        self.assertIn("a check exploded", read(os.path.join(logs, "alpha.log")))
        self.assertUntouched(a, head)
        self.assertEqual(self.verify_runs(a), [])


class TestFullVerifyOnlyWhenNeeded(FlowBase):
    def test_a_move_of_check_tools_alone_never_starts_the_full_script(self):
        a = self.make_consumer("alpha", tag="v1.0.0", verify=VERIFY_FAIL, vale="v0.3.4")
        # v1.0.0 -> v1.0.1 leaves the loader as it is; house-style moves too. VERIFY_FAIL would fail the run if it ran.
        proc = self.roll(None, "--apply", pins={"commit-checks": "v1.0.1", "house-style": "v0.3.6"})
        self.assertPasses(proc)
        self.assertEqual(self.settings(a)["version"], "v1.0.1")
        self.assertEqual(git(a, "status", "--porcelain"), "")
        self.assertNotIn("verify_commit", proc.stdout)

    def test_a_changed_loader_starts_the_full_script(self):
        a = self.make_consumer("alpha", verify=VERIFY_OK)
        self.assertPasses(self.roll("v1.1.0", "--apply"))
        self.assertEqual(self.verify_runs(a), ["run"])

    def test_a_changed_loader_failing_the_full_script_restores(self):
        a = self.make_consumer("alpha", verify=VERIFY_FAIL)
        head = self.head(a)
        self.assertBlocks(self.roll("v1.1.0", "--apply"))
        self.assertUntouched(a, head)

    def test_the_full_script_runs_for_a_library_pin(self):
        a = self.both("alpha")
        self.assertPasses(self.judarn_roll("--apply", pins={"judarn": "v1.1.0"}))
        self.assertEqual(self.verify_runs(a), ["run"])
        self.assertEqual(git(a, "log", "-1", "--format=%s"), "build: pin Judarn at v1.1.0")

    def test_commit_checks_verify_runs_first_and_sees_the_new_pin(self):
        loader = ('#!/usr/bin/env python3\nimport os\nroot = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n'
                  'open(os.path.join(root, ".git/loader-saw"), "w").write(open(os.path.join(root, ".commit-checks.json")).read())\n')
        self.add_release("v1.2.0", loader)
        a = self.make_consumer("alpha", verify=VERIFY_OK)
        self.assertPasses(self.roll("v1.2.0", "--apply"))
        self.assertIn("v1.2.0", read(os.path.join(a, ".git/loader-saw")))


class TestChecksEntry(FlowBase):
    def command(self, root, fail):
        script = "import sys; open(%r, 'a').write('ran\\n'); sys.exit(%d)" % (os.path.join(root, ".git/cheap-runs"), 3 if fail else 0)
        return [sys.executable, "-c", script]

    def test_a_checks_command_leaves_no_background_child(self):
        a = self.both("alpha", verify=VERIFY_OK)
        pid_file = os.path.join(a, ".git", "check-bg-pid")
        script = ("import subprocess, sys; p = subprocess.Popen(['sleep', '300']); open(%r, 'w').write(str(p.pid)); sys.exit(0)"
                  % pid_file)
        self.consumers[-1]["checks"] = {"judarn": [sys.executable, "-c", script]}
        proc = self.judarn_roll("--apply", pins={"judarn": "v1.1.0"})
        self.assertPasses(proc)
        pid = int(read(pid_file))
        wait_until(self, lambda: not pid_alive(pid), "the check's background child to be gone")

    def test_a_checks_entry_replaces_the_full_script(self):
        a = self.both("alpha", verify=VERIFY_FAIL)  # the full script would fail the run
        self.consumers[-1]["checks"] = {"judarn": self.command(a, fail=False)}
        proc = self.judarn_roll("--apply", pins={"judarn": "v1.1.0"})
        self.assertPasses(proc)
        self.assertEqual(read(os.path.join(a, ".git/cheap-runs")).split(), ["ran"])
        self.assertEqual(git(a, "log", "-1", "--format=%s"), "build: pin Judarn at v1.1.0")

    def test_a_failing_checks_command_restores_and_is_logged(self):
        a = self.both("alpha", verify=VERIFY_OK)
        self.consumers[-1]["checks"] = {"judarn": self.command(a, fail=True)}
        head = self.head(a)
        logs = os.path.join(self.tmp, "logs")
        proc = self.judarn_roll("--apply", "--log-dir", logs, pins={"judarn": "v1.1.0"})
        self.assertBlocks(proc)
        self.assertIn("CHECKS FAIL    alpha: the judarn check failed (exit 3)", proc.stdout)
        self.assertIn("cheap-runs", read(os.path.join(logs, "alpha.log")))
        self.assertUntouched(a, head)
        self.assertEqual(self.verify_runs(a), [])

    def test_a_changed_loader_still_needs_the_full_script(self):
        a = self.both("alpha", verify=VERIFY_OK)
        self.consumers[-1]["checks"] = {"judarn": self.command(a, fail=False)}
        proc = self.roll(None, "--apply", pins={"commit-checks": "v1.1.0", "judarn": "v1.1.0"}, env={"JUDARN_PATH": self.pkg})
        self.assertPasses(proc)
        self.assertEqual(self.verify_runs(a), ["run"])
        self.assertFalse(os.path.exists(os.path.join(a, ".git/cheap-runs")))

    def test_a_pin_without_a_checks_entry_still_needs_the_full_script(self):
        a = self.both("alpha", verify=VERIFY_OK)
        self.consumers[-1]["checks"] = {"house-style": self.command(a, fail=False)}
        self.assertPasses(self.judarn_roll("--apply", pins={"judarn": "v1.1.0"}))
        self.assertEqual(self.verify_runs(a), ["run"])

    def test_bad_checks_entries_are_errors(self):
        self.both("alpha")
        for bad in ({"judarn": "echo"}, {"judarn": []}, {"nope": ["x"]}, ["x"]):
            self.consumers[-1]["checks"] = bad
            self.assertErrors(self.judarn_roll(pins={"judarn": "v1.1.0"}))


class TestSeveralGates(FlowBase):
    def gates(self, cc, judarn):
        def gate(decision):
            body = "import json; print(json.dumps({'alpha': {'decision': %r, 'reason': 'why %s'}}))" % (decision, decision)
            return [sys.executable, "-c", body]
        return self.write_list({"gates": {"commit-checks": gate(cc), "judarn": gate(judarn)}})

    def test_each_package_has_its_own_gate_and_a_hold_covers_that_package_only(self):
        a = self.both("alpha", verify=VERIFY_OK)
        lst = self.gates("go", "hold")
        proc = self.roll(None, "--apply", list_path=lst, pins={"commit-checks": "v1.1.0", "judarn": "v1.1.0"},
                         env={"JUDARN_PATH": self.pkg})
        self.assertBlocks(proc)
        self.assertEqual(self.settings(a)["version"], "v1.1.0")  # moved
        self.assertIn("HELD", proc.stdout)
        self.assertIn("judarn (gate: why hold)", proc.stdout)
        self.assertNotIn("v1.1.0\n", read(os.path.join(a, "Package.swift")))
        self.assertEqual(git(a, "log", "-1", "--format=%s"), "ci: load the shared commit checks at v1.1.0")
        self.assertEqual(git(a, "status", "--porcelain"), "")

    def test_both_go(self):
        a = self.both("alpha", verify=VERIFY_OK)
        proc = self.roll(None, "--apply", list_path=self.gates("go", "go"), pins={"commit-checks": "v1.1.0", "judarn": "v1.1.0"},
                         env={"JUDARN_PATH": self.pkg})
        self.assertPasses(proc)
        self.assertEqual(self.settings(a)["version"], "v1.1.0")

    def test_both_held_edits_nothing(self):
        a = self.both("alpha")
        head = self.head(a)
        proc = self.roll(None, "--apply", list_path=self.gates("hold", "hold"), pins={"commit-checks": "v1.1.0", "judarn": "v1.1.0"},
                         env={"JUDARN_PATH": self.pkg})
        self.assertBlocks(proc)
        self.assertUntouched(a, head)

    def test_a_gate_runs_once_for_its_own_pin(self):
        self.both("alpha")
        count = os.path.join(self.tmp, "gate-count")
        body = "import json; open(%r, 'a').write('x'); print(json.dumps({'alpha': {'decision': 'go'}}))" % count
        lst = self.write_list({"gates": {"commit-checks": [sys.executable, "-c", body], "judarn": [sys.executable, "-c", body]}})
        self.assertPasses(self.roll(None, list_path=lst, pins={"commit-checks": "v1.1.0", "judarn": "v1.1.0"}, env={"JUDARN_PATH": self.pkg}))
        self.assertEqual(read(count), "xx")  # one run per gated package
