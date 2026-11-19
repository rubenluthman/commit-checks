import os
import shutil
import subprocess
import sys
import tempfile

from support import Base, env_for, email, home_path, conflict_marker


class HomePathTests(Base):
    CHECK = "home-paths"

    def test_home_paths_are_blocked(self):
        for text in (home_path("bob"), "/hom" + "e/alice/x", "see ~/Develop" + "er/thing", "/Use" + "rs/Åsa-1/x/"):
            with self.subTest(text=text):
                r = self.repo()
                r.stage("a.md", "path: %s\n" % text)
                self.assertBlocks(r.run("pre-commit"), "FAIL home-paths: a.md")
                self.assertBlocks(r.run("verify"), "FAIL home-paths")

    def test_home_path_in_a_commit_message_is_blocked(self):
        r = self.repo()
        r.write("MSG", "fix: moved %s\n" % home_path())
        self.assertBlocks(r.run("commit-msg", r.path("MSG")), "home-paths")

    def test_placeholders_and_ci_paths_pass(self):
        r = self.repo()
        r.stage("a.md", "/Use" + "rs/runner/work and /hom" + "e/runner/x and /Use" + "rs/name/x and a Users folder\n")
        self.assertPasses(r.run("pre-commit"))
        self.assertPasses(r.run("verify"))

    def test_a_home_path_only_in_a_removed_line_passes(self):
        r = self.repo()
        r.stage("a.md", "path: %s\n" % home_path())
        r.commit()
        r.stage("a.md", "clean\n")
        self.assertPasses(r.run("pre-commit"))


class EmailTests(Base):
    CHECK = "emails"

    def test_personal_addresses_are_blocked(self):
        for addr in (email(), email("first.last+tag", "company.co.uk"), email("x", "icloud.com")):
            with self.subTest(addr=addr):
                r = self.repo()
                r.stage("a.md", "contact %s now\n" % addr)
                self.assertBlocks(r.run("pre-commit"), "FAIL emails: a.md")
                self.assertBlocks(r.run("verify"), "FAIL emails")

    def test_email_in_a_commit_message_is_blocked(self):
        r = self.repo()
        r.write("MSG", "fix: thanks %s\n" % email())
        self.assertBlocks(r.run("commit-msg", r.path("MSG")), "emails")

    def test_allowed_forms_pass(self):
        r = self.repo()
        r.stage("a.md", "\n".join([
            email("1+x", "users.noreply.github.com"), email("noreply", "anthropic.com"),
            email("me", "example.com"), email("a", "example.test"), "git" + "@github.com:owner/repo.git", "icon" + "@2x.png", email("t", "mail.invalid"), email("t", "host.test"), ""]))
        self.assertPasses(r.run("pre-commit"))
        self.assertPasses(r.run("verify"))

    def test_the_repository_can_allow_its_own_domain(self):
        r = self.repo()
        r.stage("a.md", "mail %s\n" % email("hello", "bookclub.se"))
        self.assertBlocks(r.run("verify"))
        r.settings(allow=[{"rule": "emails", "path": "a.md", "reason": "the club's public address"}])
        self.assertPasses(r.run("verify"))


class PrivateNameTests(Base):
    CHECK = "private-names"

    def test_names_are_blocked_in_files_and_messages(self):
        r = self.repo(visibility="public")
        r.stage("a.md", "the Zebra" + "foo project\n")
        self.assertBlocks(r.run("pre-commit", use_names=True), "FAIL private-names: a.md")
        self.assertBlocks(r.run("verify", use_names=True), "FAIL private-names")
        r.write("MSG", "feat: add quux" + "BAR\n")
        self.assertBlocks(r.run("commit-msg", r.path("MSG"), use_names=True), "private-names")

    def test_clean_text_passes_with_the_list(self):
        r = self.repo(visibility="public")
        r.stage("a.md", "an ordinary line\n")
        self.assertPasses(r.run("pre-commit", use_names=True))
        self.assertPasses(r.run("verify", use_names=True))

    def test_public_repository_without_the_list_fails(self):
        r = self.repo(visibility="public")
        r.stage("a.md", "fine\n")
        self.assertErrors(r.run("pre-commit"), "private-names list is missing")
        self.assertErrors(r.run("verify"), "private-names list is missing")

    def test_going_public_repository_without_the_list_fails(self):
        r = self.repo(visibility="going-public")
        self.assertErrors(r.run("verify"), "private-names list is missing")

    def test_public_repository_with_an_empty_list_fails(self):
        r = self.repo(visibility="public")
        with open(r.names_file, "w") as fh:
            fh.write("# only a comment\n\n")
        self.assertErrors(r.run("verify", use_names=True), "private-names list is missing")

    def test_private_repository_ignores_the_list(self):
        r = self.repo(visibility="private")
        r.stage("a.md", "the zebra" + "foo project\n")
        self.assertPasses(r.run("pre-commit", use_names=True))
        self.assertPasses(r.run("verify", use_names=True))

    def test_private_repository_without_the_list_still_passes(self):
        r = self.repo(visibility="private")
        r.stage("a.md", "fine\n")
        self.assertPasses(r.run("verify"))

    def test_the_ci_secret_supplies_the_list(self):
        r = self.repo(visibility="public")
        r.stage("a.md", "the zebra" + "foo project\n")
        proc = r.run("ci", env={"COMMIT_CHECKS_PRIVATE_NAMES": "# names\nzebra" + "foo\n"})
        self.assertBlocks(proc, "private-names")
        r2 = self.repo(visibility="public")
        r2.stage("a.md", "fine\n")
        self.assertPasses(r2.run("ci", env={"COMMIT_CHECKS_PRIVATE_NAMES": "zebra" + "foo\n"}))

    def test_a_missing_ci_secret_fails(self):
        r = self.repo(visibility="public")
        self.assertErrors(r.run("ci", env={"COMMIT_CHECKS_PRIVATE_NAMES": ""}), "private-names list is missing")

    def test_ci_output_hides_the_matched_name(self):
        r = self.repo(visibility="public")
        r.stage("a.md", "the zebra" + "foo project\n")
        proc = r.run("ci", env={"COMMIT_CHECKS_PRIVATE_NAMES": "zebra" + "foo\n"})
        self.assertNotIn("zebra" + "foo", (proc.stdout + proc.stderr).lower())

    def test_a_broken_pattern_is_an_error(self):
        r = self.repo(visibility="public")
        r.stage("a.md", "x\n")
        self.assertErrors(r.run("verify", env={"COMMIT_CHECKS_PRIVATE_NAMES": "(unclosed\n"}))


class SecretFileTests(Base):
    CHECK = "secret-files"

    NAMES = [".env", ".env.local", ".dev.vars", ".dev.vars.production", "key.p8", "cert.p12", "a/b/server.key",
             "x.pem", "Dev.mobileprovision", "id_ed25519", "id_rsa", "GoogleService-Info.plist"]

    def test_secret_file_names_are_blocked(self):
        for name in self.NAMES:
            with self.subTest(name=name):
                r = self.repo()
                r.stage(name, "nothing sensitive\n") if False else None
                r.write(name, "plain\n")
                r.git("add", "-f", "--", name)
                self.assertBlocks(r.run("pre-commit"), "FAIL secret-files: " + name)
                self.assertBlocks(r.run("verify"), "FAIL secret-files")

    def test_example_and_public_key_files_pass(self):
        r = self.repo()
        for name in (".env.example", ".dev.vars.example", "id_ed25519.pub", "notes.txt"):
            r.write(name, "plain\n")
        r.git("add", "-f", "--", ".env.example", ".dev.vars.example", "id_ed25519.pub", "notes.txt")
        self.assertPasses(r.run("pre-commit"))
        self.assertPasses(r.run("verify"))

    def test_a_secret_file_already_committed_fails_verify_but_not_pre_commit(self):
        r = self.repo()
        r.write("x.pem", "plain\n")
        r.git("add", "-f", "x.pem")
        r.commit()
        r.stage("other.txt", "fine\n")
        self.assertPasses(r.run("pre-commit"))
        self.assertBlocks(r.run("verify"), "x.pem")

    def test_an_allowance_covers_a_fixture(self):
        r = self.repo()
        r.settings(allow=[{"rule": "secret-files", "path": "tests/fixtures/*.pem", "reason": "empty test fixture"}])
        r.write("tests/fixtures/a.pem", "plain\n")
        r.git("add", "-f", "tests/fixtures/a.pem")
        self.assertPasses(r.run("verify"))


class ConflictMarkerTests(Base):
    CHECK = "conflict-markers"

    def test_markers_are_blocked(self):
        for c in ("<", ">"):
            with self.subTest(marker=c):
                r = self.repo()
                r.stage("a.txt", "one\n" + conflict_marker(c) + "two\n")
                self.assertBlocks(r.run("pre-commit"), "FAIL conflict-markers: a.txt")
                self.assertBlocks(r.run("verify"), "conflict-markers")

    def test_equals_rules_and_inline_text_pass(self):
        r = self.repo()
        r.stage("a.md", "Title\n=======\nBody with <<<<<<< inside a line\n")
        self.assertPasses(r.run("pre-commit"))
        self.assertPasses(r.run("verify"))

    def test_binary_files_are_skipped(self):
        r = self.repo()
        r.write("a.bin", b"\x00\x01" + conflict_marker().encode(), binary=True)
        r.git("add", "a.bin")
        self.assertPasses(r.run("verify"))


class GitignoreBaselineTests(Base):
    CHECK = "gitignore-baseline"

    def test_missing_baseline_fails(self):
        r = self.repo()
        r.write(".gitignore", "node_modules\n")
        self.assertBlocks(r.run("verify"), "does not ignore .env")
        self.assertBlocks(r.run("pre-commit"), "gitignore-baseline")

    def test_no_gitignore_at_all_fails(self):
        r = self.repo()
        os.remove(r.path(".gitignore"))
        self.assertBlocks(r.run("verify"), "gitignore-baseline")

    def test_each_missing_line_is_reported(self):
        for drop in (".env", "*.p8", "*.pem", "id_*", ".dev.vars*"):
            with self.subTest(dropped=drop):
                r = self.repo()
                lines = [l for l in open(r.path(".gitignore")).read().split("\n") if l != drop]
                r.write(".gitignore", "\n".join(lines))
                self.assertBlocks(r.run("verify"), "gitignore-baseline")

    def test_full_baseline_passes(self):
        r = self.repo()
        self.assertPasses(r.run("verify"))

    def test_a_global_ignore_file_does_not_count(self):
        r = self.repo()
        r.write(".gitignore", "node_modules\n")
        g = r.root + "-global-ignore"
        with open(g, "w") as fh:
            fh.write(".env\n.env.*\n*.pem\n")
        self.addCleanup(os.remove, g)
        self.assertBlocks(r.run("verify", env={"GIT_CONFIG_GLOBAL": "/dev/null", "XDG_CONFIG_HOME": g}), "gitignore-baseline")


class AuthorEmailTests(Base):
    CHECK = "author-emails"

    def test_public_repository_blocks_a_personal_author_address(self):
        r = self.repo(visibility="public", email_addr=email())
        r.stage("a.txt", "fine\n")
        self.assertBlocks(r.run("pre-commit", use_names=True), "author-emails")

    def test_going_public_repository_blocks_a_personal_committer_address(self):
        r = self.repo(visibility="going-public")
        r.stage("a.txt", "fine\n")
        proc = r.run("pre-commit", use_names=True, env={"GIT_COMMITTER_EMAIL": email()})
        self.assertBlocks(proc, "author-emails")

    def test_noreply_addresses_pass(self):
        r = self.repo(visibility="public")
        r.stage("a.txt", "fine\n")
        self.assertPasses(r.run("pre-commit", use_names=True))

    def test_private_repository_allows_any_address(self):
        r = self.repo(visibility="private", email_addr=email())
        r.stage("a.txt", "fine\n")
        self.assertPasses(r.run("pre-commit"))

    def test_the_repository_can_allow_an_address(self):
        r = self.repo(visibility="public", email_addr=email("bot", "ci.test"), settings_extra={"email_allow": [r"@ci\.test$"]})
        r.stage("a.txt", "fine\n")
        self.assertPasses(r.run("pre-commit", use_names=True))

    def test_pushed_commits_are_checked(self):
        r = self.repo(visibility="public", commit_first=True)
        base = r.git("rev-parse", "HEAD").stdout.strip()
        r.stage("a.txt", "fine\n")
        r.git("-c", "user.email=" + email(), "commit", "-q", "-m", "chore: other")
        new = r.git("rev-parse", "HEAD").stdout.strip()
        proc = r.run("pre-push", use_names=True, stdin="refs/heads/main %s refs/heads/main %s\n" % (new, base))
        self.assertBlocks(proc, "author-emails")

    def test_pushed_noreply_commits_pass(self):
        r = self.repo(visibility="public", commit_first=True)
        base = r.git("rev-parse", "HEAD").stdout.strip()
        r.stage("a.txt", "fine\n")
        new = r.commit()
        proc = r.run("pre-push", use_names=True, stdin="refs/heads/main %s refs/heads/main %s\n" % (new, base))
        self.assertPasses(proc)


class HooksInstalledTests(Base):
    CHECK = "hooks-installed"

    def _install(self, r, body="exec scripts/commit-checks pre-commit\n"):
        for hook in ("pre-commit", "commit-msg", "pre-push"):
            r.write(".githooks/" + hook, "#!/bin/sh\n" + body)
            os.chmod(r.path(".githooks/" + hook), 0o755)
        r.git("config", "core.hooksPath", ".githooks")

    def test_not_installed_fails(self):
        r = self.repo()
        self.assertBlocks(r.run("verify", env={"CI": ""}), "hooks-installed")

    def test_installed_passes(self):
        r = self.repo()
        self._install(r)
        self.assertPasses(r.run("verify", env={"CI": ""}))

    def test_hooksPath_pointing_elsewhere_fails(self):
        r = self.repo()
        self._install(r)
        r.git("config", "core.hooksPath", "/dev/null")
        self.assertBlocks(r.run("verify", env={"CI": ""}), "core.hooksPath")

    def _worktree(self, r):
        r.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "chore: start")
        r.git("config", "extensions.worktreeConfig", "true")
        wt = os.path.join(tempfile.mkdtemp(prefix="cc-wt-"), "wt")
        self.addCleanup(shutil.rmtree, os.path.dirname(wt), True)
        r.git("worktree", "add", "-q", "-b", "side", wt)
        return os.path.realpath(wt)

    def _in_worktree(self, r, wt, value):
        env = env_for(extra={"CI": ""})
        subprocess.run(["git", "config", "--worktree", "core.hooksPath", value], cwd=wt, check=True, env=env)
        return subprocess.run([sys.executable, "-m", "commit_checks", "verify"], cwd=wt, capture_output=True, text=True, env=env)

    def _hooked_main(self):
        r = self.repo()
        self._install(r)
        r.git("add", ".githooks")
        return r

    def test_worktree_with_the_main_checkout_hooks_path_passes(self):
        r = self._hooked_main()
        wt = self._worktree(r)
        self.assertPasses(self._in_worktree(r, wt, os.path.join(r.root, ".githooks")))

    def test_worktree_with_its_own_absolute_hooks_path_passes(self):
        r = self._hooked_main()
        wt = self._worktree(r)
        self.assertPasses(self._in_worktree(r, wt, os.path.join(wt, ".githooks")))

    def test_worktree_with_another_absolute_path_fails(self):
        r = self._hooked_main()
        wt = self._worktree(r)
        self.assertBlocks(self._in_worktree(r, wt, tempfile.gettempdir()), "core.hooksPath")

    def test_a_hook_that_does_not_call_the_package_fails(self):
        r = self.repo()
        self._install(r, body="exit 0\n")
        self.assertBlocks(r.run("verify", env={"CI": ""}), "does not call commit-checks")

    def test_a_hook_that_is_not_executable_fails(self):
        r = self.repo()
        self._install(r)
        os.chmod(r.path(".githooks/pre-push"), 0o644)
        self.assertBlocks(r.run("verify", env={"CI": ""}), "not executable")

    def test_ci_skips_the_hook_check(self):
        r = self.repo()
        self.assertPasses(r.run("ci"))
