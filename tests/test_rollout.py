"""roll-out: throwaway package and consumer repositories in temp folders, each consumer with a bare remote."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

from support import Base, env_for

LOADER_V1 = "#!/usr/bin/env python3\n# loader one\n"
LOADER_V2 = "#!/usr/bin/env python3\n# loader two\n"
SETTINGS = '{\n  "version": "%s",\n  "commit": "%s",\n  "source": "https://example.com/pkg.git",\n  "visibility": "private"\n}\n'
HOOK_BUMP = '#!/bin/sh\necho "code changed under the released tag v1.0.0; bump the version" >&2\nexit 1\n'
HOOK_OTHER = '#!/bin/sh\necho "some other complaint" >&2\nexit 1\n'
GATE = (
    "import os, sys\n"
    "open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gate-tag.txt'), 'w').write(sys.argv[2])\n"
    "if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gate-fails')):\n"
    "    sys.exit(3)\n"
    "sys.stdout.write(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), sys.argv[1])).read())\n"
)


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

    def tag_commit(self, tag):
        return git(self.pkg, "rev-parse", tag + "^{commit}")

    def make_consumer(self, name, tag="v1.0.0", loader=LOADER_V1, agents="# Agents\n", hook=None, branch="main"):
        root = os.path.join(self.tmp, "apps", name)
        remote = os.path.join(self.tmp, "remotes", name + ".git")
        os.makedirs(root)
        git(self.tmp, "init", "-q", "--bare", remote)
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.email", "1+t@users.noreply.github.com")
        git(root, "config", "user.name", "T")
        write(os.path.join(root, ".commit-checks.json"), SETTINGS % (tag, self.tag_commit(tag)))
        write(os.path.join(root, "scripts/commit-checks"), loader, 0o755)
        write(os.path.join(root, "AGENTS.md"), agents)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "chore: start")
        git(root, "remote", "add", "origin", remote)
        git(root, "push", "-q", "origin", "main")
        if hook:
            write(os.path.join(root, ".git/hooks/pre-commit"), hook, 0o755)
        if branch != "main":
            git(root, "checkout", "-q", "-b", branch)
        entry = {"name": name, "path": root}
        self.consumers.append(entry)
        return root

    def write_list(self, extra=None, consumers=None):
        data = {"consumers": [{"name": c["name"], "path": os.path.relpath(c["path"], self.tmp)}
                              for c in (self.consumers if consumers is None else consumers)]}
        data.update(extra or {})
        path = os.path.join(self.tmp, "list.json")
        write(path, json.dumps(data))
        return path

    def roll(self, to, *flags, list_path=None, env=None):
        e = env_for(extra=dict({"COMMIT_CHECKS_PATH": self.pkg}, **(env or {})))
        return subprocess.run([sys.executable, "-m", "commit_checks", "roll-out", "--list", list_path or self.write_list(),
                               "--package", "commit-checks", "--to", to] + list(flags),
                              cwd=self.tmp, capture_output=True, text=True, env=e, timeout=300, errors="replace")

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
        self.assertEqual(git(root, "log", "-1", "--format=%s"), "build(deps): update commit-checks to v1.1.0")
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

    def test_collaborative_skip(self):
        root = self.make_consumer("alpha", agents="# Agents\n\nCollaborative: use branches and pull requests.\n")
        head = self.head(root)
        proc = self.roll("v1.1.0", "--apply")
        self.assertBlocks(proc)
        self.assertIn("collaborative", proc.stdout)
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
        git(root, "remote", "set-url", "origin", os.path.join(self.tmp, "no-such-remote.git"))
        proc = self.roll("v1.1.0", "--apply", "--push")
        self.assertBlocks(proc)
        self.assertIn("push failed", proc.stdout)
        self.assertNotIn("UPDATED", proc.stdout)

    def test_missing_folder_and_not_a_consumer(self):
        self.make_consumer("alpha")
        other = os.path.join(self.tmp, "apps", "plain")
        os.makedirs(other)
        git(other, "init", "-q", "-b", "main")
        self.consumers.append({"name": "plain", "path": other})
        self.consumers.append({"name": "gone", "path": os.path.join(self.tmp, "apps", "gone")})
        proc = self.roll("v1.1.0", "--apply")
        self.assertBlocks(proc)
        self.assertIn("BLOCKED", proc.stdout)
        self.assertIn("SKIPPED", proc.stdout)
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
        return self.write_list({"gate": [sys.executable, "gate.py", "gate.json", "{tag}"]})

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
