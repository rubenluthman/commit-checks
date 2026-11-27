"""roll-out: throwaway package and consumer repositories in temp folders, each consumer with a bare remote."""
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

from support import Base, env_for

LOADER_V1 = "#!/usr/bin/env python3\n# loader one\n"
LOADER_V2 = "#!/usr/bin/env python3\n# loader two\n"
SETTINGS = '{\n  "version": "%s",\n  "commit": "%s",\n  "source": "https://example.com/pkg.git",\n  "visibility": "private"\n}\n'
VALE = "StylesPath = .vale/styles\nPackages = https://github.com/acme/house-style/releases/download/%s/HouseStyle.zip\nMinAlertLevel = warning\n"
VERIFY_OK = ("#!/bin/sh\necho run >> .git/verify-runs\ngit diff --cached --name-only > .git/verify-staged\n"
             "cat .commit-checks.json .vale.ini > .git/verify-saw 2>/dev/null\nexit 0\n")
VERIFY_BUMP = '#!/bin/sh\necho "code changed under the released tag; bump the version"\nexit 1\n'
VERIFY_FAIL = '#!/bin/sh\necho "test suite exploded"\nexit 1\n'
# Starts a background child, as a real verify run does through heavy-run, and records its pid.
VERIFY_BG_HANG = '#!/bin/sh\nsleep 300 &\necho $! > .git/bg-pid\necho started > .git/verify-started\nwait\n'
HOOK_BG_HANG = '#!/bin/sh\nsleep 300 &\necho $! > .git/bg-pid\nwait\n'
HOOK_BG_LEAVE = '#!/bin/sh\nsleep 300 &\necho $! > .git/bg-pid\nexit 0\n'
VERIFY_BG_LEAVE = '#!/bin/sh\nsleep 300 &\necho $! > .git/bg-pid\nexit 0\n'
GH_STUB = '#!/bin/sh\necho "$@" >> "$GH_LOG"\necho https://example.com/acme/repo/pull/7\nexit 0\n'
GH_FAIL = '#!/bin/sh\necho "gh broke" >&2\nexit 1\n'
HOOK_BUMP = '#!/bin/sh\necho "code changed under the released tag v1.0.0; bump the version" >&2\nexit 1\n'
HOOK_VALE = '#!/bin/sh\nif git diff --cached --name-only | grep -q vale.ini; then echo "style commit refused" >&2; exit 1; fi\nexit 0\n'
HOOK_OTHER = '#!/bin/sh\necho "some other complaint" >&2\nexit 1\n'
GATE = (
    "import os, sys\n"
    "with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gate-tag.txt'), 'w') as fh:\n"
    "    fh.write(sys.argv[2])\n"
    "if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gate-fails')):\n"
    "    sys.exit(3)\n"
    "with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), sys.argv[1])) as fh:\n"
    "    sys.stdout.write(fh.read())\n"
)


def wait_until(test, condition, what, timeout=30.0):
    """A condition wait: fails the test when the condition is not true in time."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    test.fail("timed out waiting for " + what)


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def git(cwd, *args, check=True):
    proc = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True, env=env_for(), errors="replace")
    if check and proc.returncode != 0:
        raise AssertionError("git %s failed: %s" % (" ".join(args), proc.stderr))
    return proc.stdout.strip()


def read(path):
    with open(path) as fh:
        return fh.read()


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    if mode:
        os.chmod(path, mode)


class RollOutBase(Base):
    def setUp(self):
        super().setUp()
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="cc-rollout-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pkg, self.pkg_remote = self.make_package()
        self.style, self.style_remote = self.make_style_package()
        self.consumers = []

    def make_package(self):
        pkg = os.path.join(self.tmp, "pkg")
        remote = os.path.join(self.tmp, "pkg-remote.git")
        os.makedirs(pkg)
        git(pkg, "init", "-q", "-b", "main")
        git(pkg, "config", "user.email", "1+t@users.noreply.github.com")
        git(pkg, "config", "user.name", "T")
        write(os.path.join(pkg, "templates/loader.py"), LOADER_V1)
        write(os.path.join(pkg, "templates/githooks/pre-commit"), "#!/bin/sh\n")
        write(os.path.join(pkg, "templates/workflows/ci.yml"), "name: one\n")
        git(pkg, "add", "-A")
        git(pkg, "commit", "-q", "-m", "release one")
        git(pkg, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
        write(os.path.join(pkg, "notes.txt"), "x\n")  # a release that leaves the loader and templates alone
        git(pkg, "add", "-A")
        git(pkg, "commit", "-q", "-m", "release one.one")
        git(pkg, "tag", "-a", "v1.0.1", "-m", "v1.0.1")
        write(os.path.join(pkg, "templates/loader.py"), LOADER_V2)
        write(os.path.join(pkg, "templates/workflows/ci.yml"), "name: two\n")
        git(pkg, "add", "-A")
        git(pkg, "commit", "-q", "-m", "release two")
        git(pkg, "tag", "-a", "v1.1.0", "-m", "v1.1.0")
        git(self.tmp, "init", "-q", "--bare", remote)
        git(pkg, "remote", "add", "origin", remote)
        git(pkg, "push", "-q", "origin", "main", "--tags")
        return pkg, remote

    def make_style_package(self):
        style = os.path.join(self.tmp, "style")
        remote = os.path.join(self.tmp, "style-remote.git")
        os.makedirs(style)
        git(style, "init", "-q", "-b", "main")
        git(style, "config", "user.email", "1+t@users.noreply.github.com")
        git(style, "config", "user.name", "T")
        for i, tag in enumerate(("v0.3.4", "v0.3.6")):
            write(os.path.join(style, "rule.txt"), "%d\n" % i)
            git(style, "add", "-A")
            git(style, "commit", "-q", "-m", "style " + tag)
            git(style, "tag", "-a", tag, "-m", tag)
        git(self.tmp, "init", "-q", "--bare", remote)
        git(style, "remote", "add", "origin", remote)
        git(style, "push", "-q", "origin", "main", "--tags")
        return style, remote

    def tag_commit(self, tag):
        return git(self.pkg, "rev-parse", tag + "^{commit}")

    def make_consumer(self, name, tag="v1.0.0", loader=LOADER_V1, agents="# Agents\n", hook=None, branch="main",
                      vale=None, verify=None, checks=True, packages=None):
        root = os.path.join(self.tmp, "apps", name)
        remote = os.path.join(self.tmp, "remotes", name + ".git")
        os.makedirs(root)
        git(self.tmp, "init", "-q", "--bare", remote)
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.email", "1+t@users.noreply.github.com")
        git(root, "config", "user.name", "T")
        if checks:
            write(os.path.join(root, ".commit-checks.json"), SETTINGS % (tag, self.tag_commit(tag)))
            write(os.path.join(root, "scripts/commit-checks"), loader, 0o755)
        write(os.path.join(root, "AGENTS.md"), agents)
        if vale:
            write(os.path.join(root, ".vale.ini"), VALE % vale)
        if verify:
            write(os.path.join(root, "scripts/verify_commit.sh"), verify, 0o755)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "chore: start")
        git(root, "remote", "add", "origin", remote)
        git(root, "push", "-q", "origin", "main")
        if hook:
            write(os.path.join(root, ".git/hooks/pre-commit"), hook, 0o755)
        if branch != "main":
            git(root, "checkout", "-q", "-b", branch)
        if packages is None:
            packages = (["commit-checks"] if checks else []) + (["house-style"] if vale else [])
        entry = {"name": name, "path": root, "packages": packages}
        self.consumers.append(entry)
        return root

    def write_list(self, extra=None, consumers=None):
        data = {"consumers": [dict({"name": c["name"], "path": os.path.relpath(c["path"], self.tmp), "packages": c["packages"]},
                                   **{k: c[k] for k in ("checks", "files", "library") if c.get(k)})
                              for c in (self.consumers if consumers is None else consumers)]}
        data.update(extra or {})
        path = os.path.join(self.tmp, "list.json")
        write(path, json.dumps(data))
        return path

    def roll_command(self, to, flags, list_path=None, env=None, pins=None):
        e = env_for(extra=dict({"COMMIT_CHECKS_PATH": self.pkg, "HOUSE_STYLE_PATH": self.style}, **(env or {})))
        pins = pins if pins is not None else {"commit-checks": to}
        args = []
        for name, tag in pins.items():
            args += ["--pin", "%s=%s" % (name, tag)]
        return [sys.executable, "-m", "commit_checks", "roll-out", "--list", list_path or self.write_list()] + args + list(flags), e

    def roll(self, to, *flags, list_path=None, env=None, pins=None, stdout_path=None):
        """`to` is the commit-checks tag; `pins` ({name: tag}) replaces it when given. With `stdout_path`, roll-out's
        standard output goes to that file, which a fake verify script can read while the run goes on."""
        argv, e = self.roll_command(to, flags, list_path, env, pins)
        if stdout_path:
            with open(stdout_path, "w") as fh:
                proc = subprocess.run(argv, cwd=self.tmp, stdout=fh, stderr=subprocess.PIPE, text=True, env=e, timeout=300, errors="replace")
            proc.stdout = read(stdout_path)
            return proc
        return subprocess.run(argv, cwd=self.tmp, capture_output=True, text=True, env=e, timeout=300, errors="replace")

    def roll_started(self, to, *flags, list_path=None, env=None, pins=None, new_session=False):
        """Start roll-out without waiting for it. SIGINT is made catchable first: a test runner started in the
        background has it ignored, and a child would inherit that."""
        argv, e = self.roll_command(to, flags, list_path, env, pins)
        proc = subprocess.Popen(argv, cwd=self.tmp, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=e, errors="replace",
                                preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL), start_new_session=new_session)
        self.addCleanup(self._reap, proc)
        return proc

    @staticmethod
    def _reap(proc):
        if proc.poll() is None:
            proc.kill()
        proc.communicate()

    def settings(self, root):
        with open(os.path.join(root, ".commit-checks.json")) as fh:
            return json.load(fh)

    def head(self, root):
        return git(root, "rev-parse", "HEAD")

    def assertUntouched(self, root, head):
        self.assertEqual(self.head(root), head)
        self.assertEqual(git(root, "status", "--porcelain"), "")


class TestRollOut(RollOutBase):
    CHECK = ""

    def test_clean_update_replaces_changed_loader(self):
        root = self.make_consumer("alpha")
        before = read(os.path.join(root, ".commit-checks.json"))
        proc = self.roll("v1.1.0", "--apply")
        self.assertPasses(proc)
        self.assertIn("UPDATED", proc.stdout)
        self.assertEqual(git(root, "log", "-1", "--format=%s"), "ci: load the shared commit checks at v1.1.0")
        data = self.settings(root)
        self.assertEqual((data["version"], data["commit"]), ("v1.1.0", self.tag_commit("v1.1.0")))
        expected = before.replace("v1.0.0", "v1.1.0").replace(self.tag_commit("v1.0.0"), self.tag_commit("v1.1.0"))
        self.assertEqual(read(os.path.join(root, ".commit-checks.json")), expected)  # formatting kept
        loader = os.path.join(root, "scripts/commit-checks")
        self.assertEqual(read(loader), LOADER_V2)
        self.assertTrue(os.stat(loader).st_mode & stat.S_IXUSR)
        self.assertEqual(git(root, "status", "--porcelain"), "")
        self.assertEqual(sorted(git(root, "show", "--name-only", "--format=", "HEAD").splitlines()),
                         [".commit-checks.json", "scripts/commit-checks"])
        self.assertIn("templates/workflows/ci.yml", proc.stdout)  # reported, never copied
        self.assertFalse(os.path.exists(os.path.join(root, ".github")))

    def test_loader_left_alone_when_template_is_the_same(self):
        root = self.make_consumer("alpha")
        self.assertPasses(self.roll("v1.0.1", "--apply"))
        self.assertEqual(git(root, "show", "--name-only", "--format=", "HEAD"), ".commit-checks.json")
        self.assertEqual(self.settings(root)["version"], "v1.0.1")

    def test_already_current(self):
        root = self.make_consumer("alpha", tag="v1.1.0", loader=LOADER_V2)
        head = self.head(root)
        proc = self.roll("v1.1.0", "--apply")
        self.assertPasses(proc)
        self.assertIn("CURRENT", proc.stdout)
        self.assertUntouched(root, head)

    def test_dirty_skip(self):
        root = self.make_consumer("alpha")
        write(os.path.join(root, "scratch.txt"), "x\n")
        head = self.head(root)
        proc = self.roll("v1.1.0", "--apply")
        self.assertBlocks(proc, None)
        self.assertIn("SKIPPED", proc.stdout)
        self.assertIn("uncommitted", proc.stdout)
        self.assertEqual(self.head(root), head)
        self.assertEqual(self.settings(root)["version"], "v1.0.0")
        self.assertTrue(os.path.exists(os.path.join(root, "scratch.txt")))

    def test_dirty_tracked_file_skip(self):
        root = self.make_consumer("alpha")
        write(os.path.join(root, "AGENTS.md"), "# edited\n")
        self.assertBlocks(self.roll("v1.1.0", "--apply"))
        self.assertEqual(self.settings(root)["version"], "v1.0.0")

    def test_non_default_branch_skip(self):
        root = self.make_consumer("alpha", branch="topic")
        head = self.head(root)
        proc = self.roll("v1.1.0", "--apply")
        self.assertBlocks(proc)
        self.assertIn("default branch", proc.stdout)
        self.assertUntouched(root, head)

    def test_hook_demanding_a_version_bump_restores_files(self):
        root = self.make_consumer("alpha", hook=HOOK_BUMP)
        head = self.head(root)
        proc = self.roll("v1.1.0", "--apply")
        self.assertBlocks(proc)
        self.assertIn("NEEDS A VERSION BUMP", proc.stdout)
        self.assertIn("bump the version", proc.stdout)  # the hook's output is shown
        self.assertUntouched(root, head)
        self.assertEqual(self.settings(root)["version"], "v1.0.0")
        self.assertEqual(read(os.path.join(root, "scripts/commit-checks")), LOADER_V1)

    def test_other_hook_failure_is_blocked_and_restored(self):
        root = self.make_consumer("alpha", hook=HOOK_OTHER)
        head = self.head(root)
        proc = self.roll("v1.1.0", "--apply")
        self.assertBlocks(proc)
        self.assertIn("BLOCKED", proc.stdout)
        self.assertNotIn("VERSION BUMP", proc.stdout)
        self.assertIn("some other complaint", proc.stdout)
        self.assertUntouched(root, head)

    def test_dry_run_edits_nothing(self):
        root = self.make_consumer("alpha")
        head = self.head(root)
        proc = self.roll("v1.1.0")
        self.assertPasses(proc)
        self.assertIn("WOULD UPDATE", proc.stdout)
        self.assertUntouched(root, head)
        self.assertEqual(self.settings(root)["version"], "v1.0.0")

    def test_push_only_with_the_flag(self):
        a = self.make_consumer("alpha")
        b = self.make_consumer("beta")
        self.assertPasses(self.roll("v1.0.1", "--apply", list_path=self.write_list(consumers=[self.consumers[0]])))
        self.assertNotEqual(git(a, "rev-parse", "origin/main"), self.head(a))
        self.assertPasses(self.roll("v1.0.1", "--apply", "--push", list_path=self.write_list(consumers=[self.consumers[1]])))
        self.assertEqual(git(b, "rev-parse", "HEAD"), git(b, "ls-remote", "origin", "refs/heads/main").split()[0])
        self.assertEqual(git(a, "ls-remote", "origin", "refs/heads/main").split()[0], git(a, "rev-parse", "origin/main"))

    def test_push_needs_apply(self):
        self.make_consumer("alpha")
        self.assertErrors(self.roll("v1.1.0", "--push"))

    def test_failed_push_is_not_updated(self):
        root = self.make_consumer("alpha")
        git(root, "remote", "set-url", "--push", "origin", os.path.join(self.tmp, "no-such-remote.git"))
        proc = self.roll("v1.1.0", "--apply", "--push")
        self.assertBlocks(proc)
        self.assertIn("push failed", proc.stdout)
        self.assertNotIn("UPDATED", proc.stdout)

    def test_failed_fetch_with_push_is_blocked_and_untouched(self):
        root = self.make_consumer("alpha")
        git(root, "remote", "set-url", "origin", os.path.join(self.tmp, "no-such-remote.git"))
        head = self.head(root)
        proc = self.roll("v1.1.0", "--apply", "--push")
        self.assertBlocks(proc)
        self.assertIn("BLOCKED        alpha: git fetch failed", proc.stdout)
        self.assertNotIn("UPDATED", proc.stdout)
        self.assertUntouched(root, head)
        self.assertEqual(self.settings(root)["version"], "v1.0.0")

    def test_missing_folder_and_not_a_consumer(self):
        self.make_consumer("alpha")
        other = os.path.join(self.tmp, "apps", "plain")
        os.makedirs(other)
        git(other, "init", "-q", "-b", "main")
        self.consumers.append({"name": "plain", "path": other, "packages": ["commit-checks"]})
        self.consumers.append({"name": "gone", "path": os.path.join(self.tmp, "apps", "gone"), "packages": ["commit-checks"]})
        proc = self.roll("v1.1.0", "--apply")
        self.assertBlocks(proc)
        self.assertIn("BLOCKED        gone: folder not found", proc.stdout)
        self.assertIn("BLOCKED        plain: listed as loading commit-checks, but no pin found", proc.stdout)
        self.assertNotIn("SKIPPED", proc.stdout)
        self.assertIn("UPDATED", proc.stdout)  # the others still move

    def test_tag_missing_on_remote_edits_nothing(self):
        root = self.make_consumer("alpha")
        git(self.pkg, "tag", "-a", "v1.2.0", "-m", "v1.2.0")  # local only
        head = self.head(root)
        self.assertErrors(self.roll("v1.2.0", "--apply"), "not on the package's remote")
        self.assertUntouched(root, head)

    def test_tag_missing_locally_is_an_error(self):
        root = self.make_consumer("alpha")
        head = self.head(root)
        self.assertErrors(self.roll("v9.9.9", "--apply"))
        self.assertUntouched(root, head)

    def test_tag_moved_on_remote_edits_nothing(self):
        root = self.make_consumer("alpha")
        write(os.path.join(self.pkg, "more.txt"), "y\n")
        git(self.pkg, "add", "-A")
        git(self.pkg, "commit", "-q", "-m", "later")
        git(self.pkg, "tag", "-a", "-f", "v1.1.0", "-m", "moved")  # remote still has the old commit
        head = self.head(root)
        self.assertErrors(self.roll("v1.1.0", "--apply"), "remote")
        self.assertUntouched(root, head)

    def test_bad_tag_shape_is_an_error(self):
        self.make_consumer("alpha")
        self.assertErrors(self.roll("1.1.0"))

    def test_package_clone_without_a_remote_is_an_error(self):
        self.make_consumer("alpha")
        git(self.pkg, "remote", "remove", "origin")
        self.assertErrors(self.roll("v1.1.0", "--apply"), "origin")


class TestGate(RollOutBase):
    CHECK = ""

    def gate_list(self, decisions, fail=False, raw=None):
        write(os.path.join(self.tmp, "gate.py"), GATE)
        write(os.path.join(self.tmp, "gate.json"), raw if raw is not None else json.dumps(decisions))
        if fail:
            write(os.path.join(self.tmp, "gate-fails"), "")
        return self.write_list({"gates": {"commit-checks": [sys.executable, "gate.py", "gate.json", "{tag}"]}})

    def two(self):
        return self.make_consumer("alpha"), self.make_consumer("beta")

    def test_go_moves_and_hold_does_not(self):
        a, b = self.two()
        heads = (self.head(a), self.head(b))
        lst = self.gate_list({"alpha": {"decision": "go", "reason": "code only"}, "beta": {"decision": "hold", "reason": "visible change"}})
        proc = self.roll("v1.1.0", "--apply", list_path=lst)
        self.assertBlocks(proc)
        self.assertEqual(read(os.path.join(self.tmp, "gate-tag.txt")), "v1.1.0")  # {tag} was replaced
        self.assertEqual(self.settings(a)["version"], "v1.1.0")
        self.assertIn("HELD", proc.stdout)
        self.assertIn("visible change", proc.stdout)
        self.assertUntouched(b, heads[1])
        self.assertEqual(self.settings(b)["version"], "v1.0.0")

    def test_consumer_missing_from_gate_output_is_held(self):
        a, b = self.two()
        lst = self.gate_list({"alpha": {"decision": "go", "reason": "ok"}})
        proc = self.roll("v1.1.0", "--apply", list_path=lst)
        self.assertBlocks(proc)
        self.assertEqual(self.settings(a)["version"], "v1.1.0")
        self.assertEqual(self.settings(b)["version"], "v1.0.0")
        self.assertIn("HELD", proc.stdout)

    def test_failing_gate_holds_everything(self):
        a, b = self.two()
        proc = self.roll("v1.1.0", "--apply", list_path=self.gate_list({}, fail=True))
        self.assertBlocks(proc)
        self.assertEqual(self.settings(a)["version"], "v1.0.0")
        self.assertEqual(self.settings(b)["version"], "v1.0.0")
        self.assertEqual(proc.stdout.count("HELD"), 2)

    def test_bad_json_holds_everything(self):
        a, b = self.two()
        proc = self.roll("v1.1.0", "--apply", list_path=self.gate_list(None, raw="not json"))
        self.assertBlocks(proc)
        self.assertEqual(self.settings(a)["version"], "v1.0.0")
        self.assertEqual(self.settings(b)["version"], "v1.0.0")

    def test_unknown_decision_is_a_hold(self):
        a, _b = self.two()
        lst = self.gate_list({"alpha": {"decision": "maybe"}, "beta": {"decision": "go"}})
        self.assertBlocks(self.roll("v1.1.0", "--apply", list_path=lst))
        self.assertEqual(self.settings(a)["version"], "v1.0.0")

    def test_approve_moves_a_held_consumer(self):
        a, b = self.two()
        lst = self.gate_list({"alpha": {"decision": "go", "reason": "ok"}, "beta": {"decision": "hold", "reason": "look first"}})
        proc = self.roll("v1.1.0", "--apply", "--approve", "beta", list_path=lst)
        self.assertPasses(proc)
        self.assertEqual(self.settings(b)["version"], "v1.1.0")

    def test_approve_covers_a_failing_gate(self):
        a, b = self.two()
        lst = self.gate_list({}, fail=True)
        proc = self.roll("v1.1.0", "--apply", "--approve", "alpha", list_path=lst)
        self.assertBlocks(proc)
        self.assertEqual(self.settings(a)["version"], "v1.1.0")
        self.assertEqual(self.settings(b)["version"], "v1.0.0")

    def test_approve_of_an_unlisted_name_is_an_error(self):
        self.two()
        self.assertErrors(self.roll("v1.1.0", "--approve", "nobody"))

    def test_hold_in_dry_run_is_reported(self):
        a, _b = self.two()
        lst = self.gate_list({"alpha": {"decision": "hold", "reason": "r"}, "beta": {"decision": "go", "reason": "r"}})
        proc = self.roll("v1.1.0", list_path=lst)
        self.assertBlocks(proc)
        self.assertIn("HELD", proc.stdout)
        self.assertIn("WOULD UPDATE", proc.stdout)


class TestList(RollOutBase):
    CHECK = ""

    def test_unlisted_repositories_under_scan_root(self):
        self.make_consumer("alpha")
        self.make_consumer("beta")
        nested = os.path.join(self.tmp, "apps", "group", "gamma")
        os.makedirs(nested)
        git(nested, "init", "-q", "-b", "main")
        write(os.path.join(nested, ".commit-checks.json"), SETTINGS % ("v1.0.0", self.tag_commit("v1.0.0")))
        plain = os.path.join(self.tmp, "apps", "plain")  # a repository that does not load the package
        os.makedirs(plain)
        git(plain, "init", "-q", "-b", "main")
        hidden = os.path.join(self.tmp, "apps", ".trees", "delta")  # hidden folders are not scanned
        os.makedirs(hidden)
        git(hidden, "init", "-q", "-b", "main")
        write(os.path.join(hidden, ".commit-checks.json"), SETTINGS % ("v1.0.0", self.tag_commit("v1.0.0")))
        lst = self.write_list({"scanRoot": "apps"}, consumers=[self.consumers[0]])  # beta is unlisted too
        proc = self.roll("v1.1.0", list_path=lst)
        listed = [l for l in proc.stdout.splitlines() if l.startswith("UNLISTED")]
        self.assertEqual(sorted(l.split()[1] for l in listed), ["beta", os.path.join("group", "gamma")])

    def test_list_errors_are_errors(self):
        self.make_consumer("alpha")
        for bad in ('{"consumers": 3}', '{"consumers": [{"name": "a"}]}', "nope",
                    '{"consumers": [{"name": "a", "path": "x"}, {"name": "a", "path": "y"}]}',
                    '{"consumers": [], "gate": "run"}'):
            path = os.path.join(self.tmp, "bad.json")
            write(path, bad)
            self.assertErrors(self.roll("v1.1.0", list_path=path))

    def test_missing_list_is_an_error(self):
        self.assertErrors(self.roll("v1.1.0", list_path=os.path.join(self.tmp, "nope.json")))


class TestHouseStyle(RollOutBase):
    CHECK = ""

    def style_commit(self, tag):
        return git(self.style, "rev-parse", tag + "^{commit}")

    def test_house_style_rewrites_only_the_tag(self):
        root = self.make_consumer("alpha", vale="v0.3.4", checks=False)
        before = read(os.path.join(root, ".vale.ini"))
        proc = self.roll(None, "--apply", pins={"house-style": "v0.3.6"})
        self.assertPasses(proc)
        self.assertEqual(read(os.path.join(root, ".vale.ini")), before.replace("v0.3.4", "v0.3.6"))
        self.assertEqual(git(root, "log", "-1", "--format=%s"), "ci: load the house style at v0.3.6")
        self.assertEqual(git(root, "show", "--name-only", "--format=", "HEAD"), ".vale.ini")

    def test_vale_ini_without_a_house_style_line_is_listed_but_not_found(self):
        root = self.make_consumer("alpha", checks=False, packages=["house-style"])
        write(os.path.join(root, ".vale.ini"), "StylesPath = styles\nMinAlertLevel = warning\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "chore: vale")
        head = self.head(root)
        proc = self.roll(None, "--apply", pins={"house-style": "v0.3.6"})
        self.assertBlocks(proc)
        self.assertIn("BLOCKED        alpha: listed as loading house-style, but no pin found", proc.stdout)
        self.assertUntouched(root, head)

    def test_other_package_url_is_not_edited(self):
        root = self.make_consumer("alpha", checks=False, packages=["house-style"])
        write(os.path.join(root, ".vale.ini"), "Packages = https://github.com/acme/other-style/releases/download/v0.3.4/Other.zip\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "chore: vale")
        self.assertBlocks(self.roll(None, "--apply", pins={"house-style": "v0.3.6"}))
        self.assertIn("v0.3.4", read(os.path.join(root, ".vale.ini")))

    def test_current_house_style(self):
        root = self.make_consumer("alpha", vale="v0.3.6", checks=False)
        head = self.head(root)
        proc = self.roll(None, "--apply", pins={"house-style": "v0.3.6"})
        self.assertPasses(proc)
        self.assertIn("CURRENT", proc.stdout)
        self.assertUntouched(root, head)

    def test_house_style_tag_missing_in_clone(self):
        root = self.make_consumer("alpha", vale="v0.3.4", checks=False)
        head = self.head(root)
        self.assertErrors(self.roll(None, "--apply", pins={"house-style": "v9.9.9"}), "local package clone")
        self.assertUntouched(root, head)

    def test_house_style_tag_missing_on_origin(self):
        root = self.make_consumer("alpha", vale="v0.3.4", checks=False)
        git(self.style, "tag", "-a", "v0.3.7", "-m", "v0.3.7")
        self.assertErrors(self.roll(None, "--apply", pins={"house-style": "v0.3.7"}), "remote")
        self.assertEqual(read(os.path.join(root, ".vale.ini")), VALE % "v0.3.4")

    def test_house_style_tag_pointing_elsewhere_on_origin(self):
        self.make_consumer("alpha", vale="v0.3.4", checks=False)
        write(os.path.join(self.style, "more.txt"), "y\n")
        git(self.style, "add", "-A")
        git(self.style, "commit", "-q", "-m", "later")
        git(self.style, "tag", "-a", "-f", "v0.3.6", "-m", "moved")
        self.assertErrors(self.roll(None, "--apply", pins={"house-style": "v0.3.6"}), "remote")

    def test_no_house_style_clone_is_an_error(self):
        self.make_consumer("alpha", vale="v0.3.4", checks=False)
        self.assertErrors(self.roll(None, pins={"house-style": "v0.3.6"}, env={"HOUSE_STYLE_PATH": os.path.join(self.tmp, "none")}))


class TestSeveralPins(RollOutBase):
    CHECK = ""
    BOTH = {"commit-checks": "v1.1.0", "house-style": "v0.3.6"}

    def log_dir(self):
        return os.path.join(self.tmp, "logs")

    def test_two_pins_make_two_commits_and_one_verification(self):
        root = self.make_consumer("alpha", vale="v0.3.4", verify=VERIFY_OK)
        start = self.head(root)
        proc = self.roll(None, "--apply", "--log-dir", self.log_dir(), pins=self.BOTH)
        self.assertPasses(proc)
        subjects = git(root, "log", "--format=%s", start + "..HEAD").splitlines()
        self.assertEqual(sorted(subjects), ["ci: load the house style at v0.3.6", "ci: load the shared commit checks at v1.1.0"])
        self.assertEqual(len(subjects), 2)
        for sha in git(root, "log", "--format=%H", start + "..HEAD").splitlines():
            files = git(root, "show", "--name-only", "--format=", sha).splitlines()
            self.assertEqual(len(set(f.startswith(".vale") for f in files)), 1)  # a commit holds one pin
        self.assertEqual(read(os.path.join(root, ".git/verify-runs")).split(), ["run"])
        staged = read(os.path.join(root, ".git/verify-staged")).split()
        self.assertEqual(sorted(staged), [".commit-checks.json", ".vale.ini", "scripts/commit-checks"])  # verified the new pins
        saw = read(os.path.join(root, ".git/verify-saw"))
        self.assertIn("v1.1.0", saw)
        self.assertIn("v0.3.6", saw)
        self.assertEqual(git(root, "status", "--porcelain"), "")

    def test_only_the_pin_that_is_behind_moves(self):
        root = self.make_consumer("alpha", tag="v1.1.0", loader=LOADER_V2, vale="v0.3.4")
        start = self.head(root)
        self.assertPasses(self.roll(None, "--apply", pins=self.BOTH))
        self.assertEqual(git(root, "log", "--format=%s", start + "..HEAD"), "ci: load the house style at v0.3.6")

    def test_repository_that_loads_only_one_package(self):
        a = self.make_consumer("alpha", vale="v0.3.4", checks=False)
        b = self.make_consumer("beta")
        proc = self.roll(None, "--apply", pins=self.BOTH)
        self.assertPasses(proc)
        self.assertEqual(git(a, "log", "--format=%s", "-1"), "ci: load the house style at v0.3.6")
        self.assertEqual(git(b, "log", "--format=%s", "-1"), "ci: load the shared commit checks at v1.1.0")

    def test_failing_second_commit_undoes_the_first(self):
        root = self.make_consumer("alpha", vale="v0.3.4", hook=HOOK_VALE)
        start = self.head(root)
        proc = self.roll(None, "--apply", pins=self.BOTH)
        self.assertBlocks(proc)
        self.assertIn("style commit refused", proc.stdout)
        self.assertUntouched(root, start)
        self.assertEqual(self.settings(root)["version"], "v1.0.0")
        self.assertEqual(read(os.path.join(root, ".vale.ini")), VALE % "v0.3.4")

    def test_bad_pin_arguments_are_errors(self):
        self.make_consumer("alpha")
        self.assertErrors(self.roll(None, pins={"nope": "v1.0.0"}))
        self.assertErrors(self.roll(None, "--pin", "commit-checks=v1.0.1", pins={"commit-checks": "v1.0.0"}))
        self.assertErrors(self.roll(None, pins={"commit-checks": ""}))
        self.assertErrors(self.roll(None, pins={}))


class TestVerify(RollOutBase):
    CHECK = ""

    def test_verification_failure_restores_and_logs(self):
        root = self.make_consumer("alpha", verify=VERIFY_FAIL)
        head = self.head(root)
        logs = os.path.join(self.tmp, "logs")
        proc = self.roll("v1.1.0", "--apply", "--log-dir", logs)
        self.assertBlocks(proc)
        self.assertIn("CHECKS FAIL", proc.stdout)
        self.assertIn("test suite exploded", proc.stdout)
        self.assertIn(logs, proc.stdout)
        self.assertIn("test suite exploded", read(os.path.join(logs, "alpha.log")))
        self.assertUntouched(root, head)
        self.assertEqual(self.settings(root)["version"], "v1.0.0")
        self.assertEqual(read(os.path.join(root, "scripts/commit-checks")), LOADER_V1)

    def test_verification_asking_for_a_bump(self):
        root = self.make_consumer("alpha", verify=VERIFY_BUMP)
        head = self.head(root)
        proc = self.roll("v1.1.0", "--apply")
        self.assertBlocks(proc)
        self.assertIn("NEEDS A VERSION BUMP", proc.stdout)
        self.assertUntouched(root, head)

    def test_default_log_folder_is_printed(self):
        self.make_consumer("alpha", verify=VERIFY_OK)
        proc = self.roll("v1.1.0", "--apply")
        self.assertPasses(proc)
        line = [l for l in proc.stdout.splitlines() if l.startswith("verification logs:")][0]
        folder = line.split(": ", 1)[1]
        self.addCleanup(shutil.rmtree, folder, True)
        self.assertTrue(os.path.isfile(os.path.join(folder, "alpha.log")))

    def test_dry_run_does_not_verify(self):
        root = self.make_consumer("alpha", verify=VERIFY_FAIL)
        proc = self.roll("v1.1.0")
        self.assertPasses(proc)
        self.assertNotIn("verification logs", proc.stdout)

    def test_missing_script_is_noted_and_the_hooks_decide(self):
        root = self.make_consumer("alpha")
        proc = self.roll("v1.1.0", "--apply")
        self.assertPasses(proc)
        self.assertIn("no scripts/verify_commit.sh", proc.stdout)
        self.assertEqual(self.settings(root)["version"], "v1.1.0")

    def test_failed_verification_in_one_repository_leaves_the_others_moving(self):
        a = self.make_consumer("alpha", verify=VERIFY_FAIL)
        b = self.make_consumer("beta", verify=VERIFY_OK)
        self.assertBlocks(self.roll("v1.1.0", "--apply"))
        self.assertEqual(self.settings(a)["version"], "v1.0.0")
        self.assertEqual(self.settings(b)["version"], "v1.1.0")


class TestVerifyStop(RollOutBase):
    CHECK = ""

    def bg_pid(self, root):
        path = os.path.join(root, ".git", "bg-pid")
        wait_until(self, lambda: os.path.isfile(path) and read(path).strip(), "the fake verify's background pid")
        return int(read(path).strip())

    def test_verify_past_its_limit_is_checks_fail_and_the_whole_tree_stops(self):
        root = self.make_consumer("alpha", verify=VERIFY_BG_HANG)
        head = self.head(root)
        proc = self.roll("v1.1.0", "--apply", env={"COMMIT_CHECKS_ROLL_OUT_VERIFY_TIMEOUT": "2"})
        self.assertBlocks(proc)
        self.assertIn("CHECKS FAIL", proc.stdout)
        self.assertIn("ran past its 2 s limit and was stopped; files restored", proc.stdout)
        self.assertNotIn("timed out after", proc.stdout)
        self.assertNotIn("BLOCKED", proc.stdout)
        self.assertUntouched(root, head)
        self.assertEqual(self.settings(root)["version"], "v1.0.0")
        pid = self.bg_pid(root)
        wait_until(self, lambda: not pid_alive(pid), "the background child to be gone")

    def test_verify_under_its_limit_is_updated_and_leaves_no_child(self):
        root = self.make_consumer("alpha", verify=VERIFY_BG_LEAVE)
        proc = self.roll("v1.1.0", "--apply", env={"COMMIT_CHECKS_ROLL_OUT_VERIFY_TIMEOUT": "60"})
        self.assertPasses(proc)
        self.assertIn("UPDATED", proc.stdout)
        self.assertEqual(self.settings(root)["version"], "v1.1.0")
        pid = self.bg_pid(root)
        wait_until(self, lambda: not pid_alive(pid), "the background child to be gone")

    def test_bad_timeout_setting_is_an_error(self):
        self.make_consumer("alpha", verify=VERIFY_OK)
        for bad in ("soon", "0", "-5"):
            self.assertErrors(self.roll("v1.1.0", "--apply", env={"COMMIT_CHECKS_ROLL_OUT_VERIFY_TIMEOUT": bad}),
                              "COMMIT_CHECKS_ROLL_OUT_VERIFY_TIMEOUT")


class TestCommitStop(RollOutBase):
    CHECK = ""

    def bg_pid(self, root):
        path = os.path.join(root, ".git", "bg-pid")
        wait_until(self, lambda: os.path.isfile(path) and read(path).strip(), "the hook's background pid")
        return int(read(path).strip())

    def test_a_commit_hook_past_its_limit_is_checks_fail_and_the_run_goes_on(self):
        a = self.make_consumer("alpha", hook=HOOK_BG_HANG)
        b = self.make_consumer("beta")
        head = self.head(a)
        proc = self.roll("v1.1.0", "--apply", env={"COMMIT_CHECKS_ROLL_OUT_VERIFY_TIMEOUT": "2"})
        self.assertBlocks(proc)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("CHECKS FAIL    alpha: the commit hook ran past its 2 s limit and was stopped; files restored", proc.stdout)
        self.assertIn("UPDATED        beta", proc.stdout)  # the run reached the next consumer
        self.assertUntouched(a, head)
        self.assertEqual(self.settings(a)["version"], "v1.0.0")
        self.assertEqual(self.settings(b)["version"], "v1.1.0")
        pid = self.bg_pid(a)
        wait_until(self, lambda: not pid_alive(pid), "the hook's background child to be gone")

    def test_a_commit_hook_under_its_limit_is_updated_and_leaves_no_child(self):
        a = self.make_consumer("alpha", hook=HOOK_BG_LEAVE)
        proc = self.roll("v1.1.0", "--apply", env={"COMMIT_CHECKS_ROLL_OUT_VERIFY_TIMEOUT": "60"})
        self.assertPasses(proc)
        self.assertIn("UPDATED        alpha", proc.stdout)
        self.assertEqual(self.settings(a)["version"], "v1.1.0")
        pid = self.bg_pid(a)
        wait_until(self, lambda: not pid_alive(pid), "the hook's background child to be gone")


class TestLiveOutput(RollOutBase):
    CHECK = ""
    COPY_OUT = '#!/bin/sh\ncp "$ROLL_OUT_STDOUT" .git/out-so-far\nexit 0\n'

    def test_each_line_is_written_as_its_repository_ends(self):
        self.make_consumer("alpha", verify=VERIFY_OK)
        b = self.make_consumer("beta", verify=self.COPY_OUT)
        out_path = os.path.join(self.tmp, "stdout.txt")
        proc = self.roll("v1.1.0", "--apply", stdout_path=out_path, env={"ROLL_OUT_STDOUT": out_path})
        self.assertPasses(proc)
        seen = read(os.path.join(b, ".git", "out-so-far"))  # what roll-out had written while beta verified
        self.assertTrue(seen.startswith("roll-out commit-checks v1.1.0 (apply)\n"), seen)  # the header comes first
        self.assertRegex(seen, r"(?m)^START          alpha: at \d\d:\d\d; log: alpha\.log$")
        self.assertRegex(seen, r"(?m)^UPDATED        alpha: .*not pushed \(<?\d+ min\)$")
        self.assertRegex(seen, r"(?m)^START          beta: at \d\d:\d\d; log: beta\.log$")
        self.assertNotIn("UPDATED        beta", seen)  # beta had not ended yet

    def test_the_header_comes_before_the_gate(self):
        a = self.make_consumer("alpha")
        out_path = os.path.join(self.tmp, "stdout.txt")
        write(os.path.join(self.tmp, "gate.py"), "import os, sys\nwith open(os.environ['ROLL_OUT_STDOUT']) as fh:\n    saw = fh.read()\n"
              "with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gate-saw.txt'), 'w') as fh:\n    fh.write(saw)\nsys.stdout.write('{\"alpha\": {\"decision\": \"go\", \"reason\": \"r\"}}')\n")
        lst = self.write_list({"gates": {"commit-checks": [sys.executable, "gate.py", "{tag}", "{pins}"]}})
        proc = self.roll("v1.1.0", list_path=lst, stdout_path=out_path, env={"ROLL_OUT_STDOUT": out_path})
        self.assertPasses(proc)
        self.assertEqual(read(os.path.join(self.tmp, "gate-saw.txt")), "roll-out commit-checks v1.1.0 (dry run, nothing edited)\n")

    def test_a_dry_run_prints_no_start_line_and_no_duration(self):
        self.make_consumer("alpha")
        self.make_consumer("beta")
        proc = self.roll("v1.1.0")
        self.assertPasses(proc)
        self.assertNotIn("START", proc.stdout)
        self.assertNotIn(" min)", proc.stdout)

    def test_only_repositories_that_are_edited_get_a_start_line(self):
        self.make_consumer("alpha", tag="v1.1.0", loader=LOADER_V2)  # current
        self.make_consumer("beta")
        write(os.path.join(self.consumers[1]["path"], "scratch.txt"), "x\n")  # skipped: uncommitted
        self.make_consumer("gamma", verify=VERIFY_OK)
        proc = self.roll("v1.1.0", "--apply")
        self.assertBlocks(proc)
        starts = [l.split()[1] for l in proc.stdout.splitlines() if l.startswith("START")]
        self.assertEqual(starts, ["gamma:"])
        self.assertIn("SKIPPED        beta: has uncommitted changes\n", proc.stdout)  # no duration on a line that had no START
        self.assertRegex(proc.stdout, r"(?m)^UPDATED        gamma: .* \(<?\d+ min\)$")

    def test_a_failed_repository_line_ends_with_its_duration(self):
        self.make_consumer("alpha", verify=VERIFY_FAIL)
        proc = self.roll("v1.1.0", "--apply")
        self.assertBlocks(proc)
        self.assertRegex(proc.stdout, r"(?m)^CHECKS FAIL    alpha: .*log: \S+alpha\.log \(<?\d+ min\)$")

    def test_the_summary_names_who_needs_attention(self):
        self.make_consumer("alpha")
        b = self.make_consumer("beta")
        write(os.path.join(b, "scratch.txt"), "x\n")
        self.make_consumer("gamma")
        git(self.consumers[2]["path"], "checkout", "-q", "-b", "topic")
        proc = self.roll("v1.1.0")
        self.assertBlocks(proc)
        self.assertIn("3 consumer(s); 2 need attention: beta, gamma.\n", proc.stdout)

    def test_the_summary_names_nobody_when_all_is_well(self):
        self.make_consumer("alpha")
        proc = self.roll("v1.1.0")
        self.assertPasses(proc)
        self.assertIn("1 consumer(s); 0 need attention.\n", proc.stdout)

    def test_the_summary_names_an_unlisted_repository(self):
        self.make_consumer("alpha")
        self.make_consumer("beta")
        proc = self.roll("v1.1.0", list_path=self.write_list({"scanRoot": "apps"}, consumers=[self.consumers[0]]))
        self.assertBlocks(proc)
        self.assertIn("1 consumer(s); 1 need attention: beta.\n", proc.stdout)


class TestCollaborative(RollOutBase):
    CHECK = ""
    AGENTS = "# Agents\n\nCollaborative: use branches and pull requests.\n"

    def gh_env(self, script=GH_STUB):
        stub = os.path.join(self.tmp, "stub-bin")
        write(os.path.join(stub, "gh"), script, 0o755)
        return {"PATH": stub + os.pathsep + os.environ.get("PATH", ""), "GH_LOG": os.path.join(self.tmp, "gh.log")}

    def test_apply_commits_on_a_branch_and_switches_back(self):
        root = self.make_consumer("alpha", agents=self.AGENTS, verify=VERIFY_OK)
        head = self.head(root)
        proc = self.roll("v1.1.0", "--apply")
        self.assertPasses(proc)
        self.assertIn("UPDATED", proc.stdout)
        self.assertEqual(git(root, "symbolic-ref", "--short", "HEAD"), "main")
        self.assertUntouched(root, head)
        self.assertEqual(self.settings(root)["version"], "v1.0.0")
        self.assertEqual(git(root, "log", "-1", "--format=%s", "ci/shared-pins"), "ci: load the shared commit checks at v1.1.0")
        self.assertEqual(git(root, "rev-parse", "ci/shared-pins~1"), head)

    def test_push_pushes_the_branch_and_opens_a_pull_request(self):
        root = self.make_consumer("alpha", agents=self.AGENTS)
        head = self.head(root)
        env = self.gh_env()
        proc = self.roll("v1.1.0", "--apply", "--push", env=env)
        self.assertPasses(proc)
        self.assertIn("https://example.com/acme/repo/pull/7", proc.stdout)
        self.assertEqual(git(root, "ls-remote", "origin", "refs/heads/main").split()[0], head)  # the default branch is untouched
        self.assertEqual(git(root, "ls-remote", "origin", "refs/heads/ci/shared-pins").split()[0], git(root, "rev-parse", "ci/shared-pins"))
        calls = read(env["GH_LOG"])
        self.assertIn("pr create", calls)
        self.assertIn("--head ci/shared-pins", calls)
        self.assertNotIn("merge", calls)

    def test_failing_gh_is_blocked(self):
        root = self.make_consumer("alpha", agents=self.AGENTS)
        proc = self.roll("v1.1.0", "--apply", "--push", env=self.gh_env(GH_FAIL))
        self.assertBlocks(proc)
        self.assertIn("pull request failed", proc.stdout)
        self.assertNotIn("UPDATED", proc.stdout)

    def test_failed_verification_removes_the_branch(self):
        root = self.make_consumer("alpha", agents=self.AGENTS, verify=VERIFY_FAIL)
        head = self.head(root)
        self.assertBlocks(self.roll("v1.1.0", "--apply"))
        self.assertUntouched(root, head)
        self.assertEqual(git(root, "branch", "--list", "ci/shared-pins"), "")
        self.assertEqual(git(root, "symbolic-ref", "--short", "HEAD"), "main")

    def test_existing_branch_is_a_skip(self):
        root = self.make_consumer("alpha", agents=self.AGENTS)
        git(root, "branch", "ci/shared-pins")
        head = self.head(root)
        proc = self.roll("v1.1.0", "--apply")
        self.assertBlocks(proc)
        self.assertIn("already exists", proc.stdout)
        self.assertUntouched(root, head)

    def test_dry_run_says_it_would_open_a_pull_request(self):
        root = self.make_consumer("alpha", agents=self.AGENTS)
        proc = self.roll("v1.1.0")
        self.assertPasses(proc)
        self.assertIn("pull request", proc.stdout)
        self.assertEqual(git(root, "branch", "--list", "ci/shared-pins"), "")


class TestPushSafety(RollOutBase):
    CHECK = ""

    def remote_commit(self, root, name):
        """A commit pushed to origin by someone else."""
        other = os.path.join(self.tmp, "other-" + name)
        git(self.tmp, "clone", "-q", "-b", "main", git(root, "remote", "get-url", "origin"), other)
        git(other, "config", "user.email", "1+t@users.noreply.github.com")
        git(other, "config", "user.name", "T")
        write(os.path.join(other, "theirs.txt"), "t\n")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "their work")
        git(other, "push", "-q", "origin", "main")

    def local_commit(self, root):
        write(os.path.join(root, "mine.txt"), "m\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "my unpushed work")

    def test_ahead_of_origin_is_skipped(self):
        root = self.make_consumer("alpha")
        self.local_commit(root)
        head = self.head(root)
        proc = self.roll("v1.1.0", "--apply", "--push")
        self.assertBlocks(proc)
        self.assertIn("ahead of origin", proc.stdout)
        self.assertUntouched(root, head)

    def test_behind_origin_is_skipped(self):
        root = self.make_consumer("alpha")
        self.remote_commit(root, "alpha")
        head = self.head(root)
        proc = self.roll("v1.1.0", "--apply", "--push")
        self.assertBlocks(proc)
        self.assertIn("behind origin", proc.stdout)
        self.assertUntouched(root, head)

    def test_diverged_is_skipped(self):
        root = self.make_consumer("alpha")
        self.remote_commit(root, "alpha")
        self.local_commit(root)
        head = self.head(root)
        proc = self.roll("v1.1.0", "--apply", "--push")
        self.assertBlocks(proc)
        self.assertIn("diverged", proc.stdout)
        self.assertUntouched(root, head)

    def test_in_sync_is_pushed(self):
        root = self.make_consumer("alpha")
        self.assertPasses(self.roll("v1.1.0", "--apply", "--push"))
        self.assertEqual(git(root, "ls-remote", "origin", "refs/heads/main").split()[0], self.head(root))

    def test_ahead_is_only_checked_when_pushing(self):
        root = self.make_consumer("alpha")
        self.local_commit(root)
        self.assertPasses(self.roll("v1.1.0", "--apply"))
        self.assertEqual(self.settings(root)["version"], "v1.1.0")


class TestUnlisted(RollOutBase):
    CHECK = ""

    def test_unlisted_consumer_fails_the_run(self):
        self.make_consumer("alpha")
        self.make_consumer("beta", checks=False, vale="v0.3.4")  # loads only the house style
        lst = self.write_list({"scanRoot": "apps"}, consumers=[self.consumers[0]])
        proc = self.roll(None, list_path=lst, pins={"commit-checks": "v1.1.0", "house-style": "v0.3.6"})
        self.assertBlocks(proc)
        self.assertIn("UNLISTED       beta loads house-style", proc.stdout)

    def test_complete_list_passes(self):
        self.make_consumer("alpha")
        self.make_consumer("beta")
        lst = self.write_list({"scanRoot": "apps"})
        self.assertPasses(self.roll("v1.1.0", list_path=lst))

    def test_repository_that_loads_no_given_package_is_not_unlisted(self):
        self.make_consumer("alpha")
        self.make_consumer("beta", vale="v0.3.4", checks=False)
        lst = self.write_list({"scanRoot": "apps"}, consumers=[self.consumers[0]])
        self.assertPasses(self.roll("v1.1.0", list_path=lst))
