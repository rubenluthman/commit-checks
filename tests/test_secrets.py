import os
import unittest

from support import Base, fake_keys, fake_gitleaks_dir, path_with, rnd

KEYS = fake_keys()


class SecretsTests(Base):
    CHECK = "secrets"

    def test_every_key_shape_is_blocked_when_staged(self):
        for name, key in KEYS.items():
            with self.subTest(shape=name):
                r = self.repo()
                r.stage("src/config.txt", "value = %s\n" % key)
                self.assertBlocks(r.run("pre-commit"), "FAIL secrets: src/config.txt")

    def test_every_key_shape_is_blocked_in_the_snapshot(self):
        for name, key in KEYS.items():
            with self.subTest(shape=name):
                r = self.repo()
                r.stage("src/config.txt", "value = %s\n" % key)
                self.assertBlocks(r.run("verify"), "FAIL secrets")

    def test_every_key_shape_is_blocked_in_a_pushed_commit(self):
        for name, key in KEYS.items():
            with self.subTest(shape=name):
                r = self.repo(commit_first=True)
                r.stage("a.txt", "x = %s\n" % key)
                sha = r.commit()
                proc = r.run("pre-push", stdin="refs/heads/main %s refs/heads/main %s\n" % (sha, "0" * 40))
                self.assertBlocks(proc, "FAIL secrets")

    def test_matched_text_is_never_printed(self):
        key = KEYS["openai-proj"]
        r = self.repo()
        r.stage("a.txt", "k = %s\n" % key)
        proc = r.run("pre-commit")
        self.assertBlocks(proc)
        self.assertNotIn(key, proc.stdout + proc.stderr)
        self.assertNotIn(key[10:30], proc.stdout + proc.stderr)

    def test_one_match_among_six_thousand_files(self):
        r = self.repo()
        for i in range(6000):
            r.write("src/d%d/f%d.txt" % (i % 50, i), "line %d\n" % i)
        r.stage("src/d7/hit.txt", "k = %s\n" % KEYS["github-token"])
        r.git("add", "-A")
        proc = r.run("verify")
        self.assertBlocks(proc, "src/d7/hit.txt")

    def test_six_thousand_clean_files_pass(self):
        r = self.repo()
        for i in range(6000):
            r.write("src/d%d/f%d.txt" % (i % 50, i), "line %d\n" % i)
        r.git("add", "-A")
        self.assertPasses(r.run("verify"))

    def test_staged_file_deleted_on_disk_is_still_found(self):
        r = self.repo()
        r.stage("a.txt", "k = %s\n" % KEYS["anthropic"])
        os.remove(r.path("a.txt"))
        self.assertBlocks(r.run("pre-commit"), "a.txt")
        self.assertBlocks(r.run("verify"), "a.txt")

    def test_secret_staged_then_changed_on_disk_is_found(self):
        r = self.repo()
        r.stage("a.txt", "k = %s\n" % KEYS["aws"])
        r.write("a.txt", "clean now\n")
        self.assertBlocks(r.run("verify"), "a.txt")

    def test_secret_only_in_the_working_copy_is_not_what_is_scanned(self):
        r = self.repo()
        r.stage("a.txt", "clean\n")
        r.write("a.txt", "k = %s\n" % KEYS["aws"])  # unstaged edit
        self.assertPasses(r.run("verify"))
        self.assertPasses(r.run("pre-commit"))

    def test_secret_only_in_a_removed_line_passes(self):
        r = self.repo()
        r.stage("a.txt", "k = %s\n" % KEYS["openai-proj"])
        r.commit()
        r.stage("a.txt", "clean\n")
        self.assertPasses(r.run("pre-commit"))
        self.assertPasses(r.run("verify"))

    def test_removal_commit_alone_passes_in_a_push_range(self):
        r = self.repo(commit_first=True)
        r.stage("a.txt", "k = %s\n" % KEYS["openai-proj"])
        old = r.commit()
        r.stage("a.txt", "clean\n")
        new = r.commit()
        proc = r.run("pre-push", stdin="refs/heads/main %s refs/heads/main %s\n" % (new, old))
        self.assertPasses(proc)

    def test_a_secret_added_then_removed_before_push_is_found(self):
        r = self.repo(commit_first=True)
        base = r.git("rev-parse", "HEAD").stdout.strip()
        r.stage("a.txt", "k = %s\n" % KEYS["openai-proj"])
        r.commit()
        r.stage("a.txt", "clean\n")
        new = r.commit()
        proc = r.run("pre-push", stdin="refs/heads/main %s refs/heads/main %s\n" % (new, base))
        self.assertBlocks(proc, "FAIL secrets")

    def test_new_branch_push_scans_all_unpushed_commits(self):
        r = self.repo(commit_first=True)
        r.stage("a.txt", "k = %s\n" % KEYS["npm"])
        r.commit()
        r.stage("a.txt", "clean\n")
        new = r.commit()
        proc = r.run("pre-push", stdin="refs/heads/topic %s refs/heads/topic %s\n" % (new, "0" * 40))
        self.assertBlocks(proc, "FAIL secrets")

    def test_branch_deletion_push_passes(self):
        r = self.repo(commit_first=True)
        proc = r.run("pre-push", stdin="(delete) %s refs/heads/old %s\n" % ("0" * 40, "a" * 40))
        self.assertPasses(proc)

    def test_secret_in_a_commit_message_is_blocked(self):
        r = self.repo()
        path = r.path("MSG")
        r.write("MSG", "fix: use token %s\n" % KEYS["github-token"])
        self.assertBlocks(r.run("commit-msg", path), "commit message")

    def test_secret_in_a_pushed_commit_message_is_blocked(self):
        r = self.repo(commit_first=True)
        base = r.git("rev-parse", "HEAD").stdout.strip()
        r.stage("a.txt", "clean\n")
        r.git("commit", "-q", "-m", "fix: key " + KEYS["stripe-live"])
        new = r.git("rev-parse", "HEAD").stdout.strip()
        proc = r.run("pre-push", stdin="refs/heads/main %s refs/heads/main %s\n" % (new, base))
        self.assertBlocks(proc, "commit messages")

    def test_clean_commit_message_passes(self):
        r = self.repo()
        r.write("MSG", "fix(editor): keep the cursor on undo\n\nBody text.\n")
        self.assertPasses(r.run("commit-msg", r.path("MSG")))

    def test_odd_file_names(self):
        for name in ("with space.txt", "new\nline.txt", "åäö/ärlig fil.txt", "-dash.txt", "quote\"d.txt"):
            with self.subTest(name=name):
                r = self.repo()
                r.stage(name, "k = %s\n" % KEYS["github-token"])
                self.assertBlocks(r.run("pre-commit"))
                self.assertBlocks(r.run("verify"))
                clean = self.repo()
                clean.stage(name, "nothing here\n")
                self.assertPasses(clean.run("pre-commit"))
                self.assertPasses(clean.run("verify"))

    def test_empty_repository_passes_verify(self):
        r = self.repo()
        r.git("rm", "-q", "--cached", ".commit-checks.json", ".gitignore")
        self.assertPasses(r.run("verify"))

    def test_first_commit_of_an_empty_repository_is_scanned(self):
        r = self.repo()
        r.stage("a.txt", "k = %s\n" % KEYS["aws"])
        self.assertBlocks(r.run("pre-commit"))
        ok = self.repo()
        ok.stage("a.txt", "fine\n")
        self.assertPasses(ok.run("pre-commit"))

    def test_git_worktree(self):
        r = self.repo(commit_first=True)
        wt = r.root + "-wt"
        r.git("worktree", "add", "-q", wt, "-b", "topic")
        try:
            with open(os.path.join(wt, "a.txt"), "w") as fh:
                fh.write("k = %s\n" % KEYS["aws"])
            r.git("-C", wt, "add", "a.txt")
            self.assertBlocks(r.run("pre-commit", cwd=wt))
            self.assertBlocks(r.run("verify", cwd=wt))
            os.remove(os.path.join(wt, "a.txt"))
            r.git("-C", wt, "reset", "-q")
            self.assertPasses(r.run("pre-commit", cwd=wt))
        finally:
            import shutil
            shutil.rmtree(wt, ignore_errors=True)

    def test_inline_allow_comments_and_ignore_files_are_not_honoured(self):
        r = self.repo()
        r.stage("a.txt", "k = %s  # gitleaks:allow\n" % KEYS["github-token"])
        r.stage(".gitleaksignore", "a.txt:github-token:1\n")
        self.assertBlocks(r.run("pre-commit"))
        self.assertBlocks(r.run("verify"))

    def test_a_written_allowance_with_a_reason_passes_that_rule_and_path_only(self):
        r = self.repo()
        r.settings(allow=[{"rule": "secrets", "path": "docs/*.md", "reason": "documented example"}])
        r.stage("docs/a.md", "k = %s\n" % KEYS["github-token"])
        self.assertPasses(r.run("verify"))
        r.stage("src/a.txt", "k = %s\n" % KEYS["github-token"])
        self.assertBlocks(r.run("verify"), "src/a.txt")

    def test_an_allowance_without_a_reason_is_an_error(self):
        r = self.repo()
        r.settings(allow=[{"rule": "cc-github-token", "path": "docs/*.md"}])
        self.assertErrors(r.run("verify"), "reason")

    def test_missing_settings_file_is_an_error(self):
        r = self.repo()
        os.remove(r.path(".commit-checks.json"))
        self.assertErrors(r.run("verify"), "missing .commit-checks.json")


class EngineFailureTests(Base):
    CHECK = "engine"

    def _repo_with_leak(self):
        r = self.repo()
        r.stage("a.txt", "k = %s\n" % KEYS["aws"])
        return r

    def _run_with(self, body, modes=("pre-commit", "verify"), env=None):
        r = self._repo_with_leak()
        d = fake_gitleaks_dir(body)
        self.addCleanup(__import__("shutil").rmtree, d, True)
        return [r.run(m, path=path_with(d), env=env) for m in modes]

    def test_gitleaks_missing_is_an_error(self):
        for proc in self._run_with(None):
            self.assertErrors(proc, "gitleaks is not installed")

    def test_wrong_version_is_an_error(self):
        for proc in self._run_with('echo "8.29.0"'):
            self.assertErrors(proc, "8.30.1 is required")

    def test_crash_is_an_error(self):
        body = 'if [ "$1" = version ]; then echo 8.30.1; exit 0; fi\necho boom >&2\nexit 139'
        for proc in self._run_with(body):
            self.assertErrors(proc, "gitleaks failed")

    def test_clean_exit_without_a_report_is_an_error(self):
        body = 'if [ "$1" = version ]; then echo 8.30.1; exit 0; fi\nexit 0'
        for proc in self._run_with(body):
            self.assertErrors(proc, "no report")

    def test_garbage_report_is_an_error(self):
        body = ('if [ "$1" = version ]; then echo 8.30.1; exit 0; fi\n'
                'while [ $# -gt 0 ]; do if [ "$1" = --report-path ]; then echo "not json" > "$2"; fi; shift; done\nexit 0')
        for proc in self._run_with(body):
            self.assertErrors(proc, "unreadable")

    def test_clean_exit_with_findings_is_an_error(self):
        body = ('if [ "$1" = version ]; then echo 8.30.1; exit 0; fi\n'
                'while [ $# -gt 0 ]; do if [ "$1" = --report-path ]; then echo \'[{"RuleID":"x","File":"a"}]\' > "$2"; fi; shift; done\nexit 0')
        for proc in self._run_with(body):
            self.assertErrors(proc, "exited cleanly")

    def test_leak_exit_without_findings_is_an_error(self):
        body = ('if [ "$1" = version ]; then echo 8.30.1; exit 0; fi\n'
                'while [ $# -gt 0 ]; do if [ "$1" = --report-path ]; then echo "[]" > "$2"; fi; shift; done\nexit 2')
        for proc in self._run_with(body):
            self.assertErrors(proc, "no findings")

    def test_hang_is_killed_and_fails(self):
        body = 'if [ "$1" = version ]; then echo 8.30.1; exit 0; fi\nsleep 30'
        for proc in self._run_with(body, modes=("pre-commit",), env={"COMMIT_CHECKS_TIMEOUT": "2"}):
            self.assertErrors(proc, "did not finish")

    def test_a_bad_timeout_setting_is_an_error(self):
        for proc in self._run_with(None, modes=("verify",), env={"COMMIT_CHECKS_TIMEOUT": "soon"}):
            self.assertErrors(proc)

    def test_a_git_error_is_an_error(self):
        r = self.repo()
        d = self._not_a_repo()
        proc = r.run("verify", cwd=d)
        self.assertErrors(proc)

    def _not_a_repo(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="cc-notrepo-")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        return d


if __name__ == "__main__":
    unittest.main()
