import io
import os
import shutil
import stat
import tarfile
import tempfile
import zipfile

from support import Base, GITLEAKS, fake_keys, fake_gitleaks_dir, path_with

KEYS = fake_keys()
STYLE_RULE = "extends: existence\nmessage: \"Avoid '%s'.\"\nlevel: error\nignorecase: true\ntokens:\n  - foo" + "bar\n"


def vale_repo(test, **kw):
    r = test.repo(**kw)
    r.write(".vale.ini", "StylesPath = .vale/styles\nMinAlertLevel = suggestion\nPackages = fake-pin-1\n\n[*.{md,txt}]\nBasedOnStyles = HouseStyle\n")
    r.write(".vale/styles/HouseStyle/Banned.yml", STYLE_RULE)
    r.write(".vale/styles/HouseStyle/.pin", "Packages = fake-pin-1\n")
    r.git("add", ".vale.ini")
    return r


class ValeTests(Base):
    CHECK = "vale"

    def test_a_banned_word_fails(self):
        r = vale_repo(self)
        r.stage("a.md", "This is a foo" + "bar sentence.\n")
        self.assertBlocks(r.run("verify"), "FAIL vale: a.md:1")

    def test_clean_prose_passes(self):
        r = vale_repo(self)
        r.stage("a.md", "This is fine.\n")
        self.assertPasses(r.run("verify"))

    def test_no_vale_config_means_no_check(self):
        r = self.repo()
        r.stage("a.md", "foo" + "bar\n")
        self.assertPasses(r.run("verify"))

    def test_an_ini_without_a_shared_package_is_skipped(self):
        r = vale_repo(self)
        r.write(".vale.ini", "StylesPath = .\n\n[*.md]\nBasedOnStyles = HouseStyle\n")
        r.git("add", ".vale.ini")
        r.stage("a.md", "foo" + "bar\n")
        self.assertPasses(r.run("verify"))

    def test_globs_and_excludes(self):
        r = vale_repo(self)
        r.stage("a.txt", "foo" + "bar\n")
        r.stage("docs/audit.md", "foo" + "bar\n")
        r.settings(vale={"globs": ["*.md"], "exclude": ["docs/*"]})
        self.assertPasses(r.run("verify"))
        r.settings(vale={"globs": ["*.md", "*.txt"], "exclude": ["docs/*"]})
        self.assertBlocks(r.run("verify"), "a.txt")

    def _with_fake_vale(self, r, body, env=None):
        d = fake_gitleaks_dir(None)
        self.addCleanup(shutil.rmtree, d, True)
        os.symlink(GITLEAKS, os.path.join(d, "gitleaks"))
        if body is not None:
            p = os.path.join(d, "vale")
            with open(p, "w") as fh:
                fh.write("#!/bin/sh\n" + body + "\n")
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
        return r.run("verify", path=path_with(d), env=env)

    def test_vale_missing_is_an_error(self):
        r = vale_repo(self)
        r.stage("a.md", "fine\n")
        self.assertErrors(self._with_fake_vale(r, None), "vale is not installed")

    def test_vale_missing_with_contributing_is_a_notice_locally_and_an_error_in_ci(self):
        r = vale_repo(self)
        r.stage("CONTRIBUTING.md", "Contribute.\n")
        for hook in ("pre-commit", "commit-msg", "pre-push"):  # local runs also check the hooks
            r.write(".githooks/" + hook, "#!/bin/sh\n# commit-checks\n")
            os.chmod(r.path(".githooks/" + hook), 0o755)
        r.git("config", "core.hooksPath", ".githooks")
        proc = self._with_fake_vale(r, None, env={"CI": ""})
        self.assertPasses(proc)
        self.assertIn("NOTICE", proc.stderr)
        self.assertErrors(self._with_fake_vale(r, None, env={"CI": "1"}), "vale is not installed")

    def test_a_changed_pin_triggers_a_failing_sync(self):
        r = vale_repo(self)
        r.write(".vale/styles/HouseStyle/.pin", "Packages = something-else\n")
        r.stage("a.md", "fine\n")
        self.assertErrors(self._with_fake_vale(r, "exit 1"), "vale sync failed")

    def test_a_crash_is_an_error(self):
        r = vale_repo(self)
        r.stage("a.md", "fine\n")
        self.assertErrors(self._with_fake_vale(r, "exit 2"), "vale failed")

    def test_a_failure_with_unreadable_output_is_an_error(self):
        r = vale_repo(self)
        r.stage("a.md", "fine\n")
        self.assertErrors(self._with_fake_vale(r, "echo nonsense; exit 1"), "no readable alerts")


def make_tar(path, members, mode="w:gz"):
    with tarfile.open(path, mode) as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


class ArchiveTests(Base):
    CHECK = "archive"

    def archive(self, kind, members):
        d = tempfile.mkdtemp(prefix="cc-arch-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "pkg." + kind)
        if kind == "zip":
            with zipfile.ZipFile(path, "w") as zf:
                for n, b in members.items():
                    zf.writestr(n, b)
        else:
            make_tar(path, members)
        return path

    def test_a_secret_in_a_tarball_or_zip_fails(self):
        for kind in ("tgz", "zip"):
            with self.subTest(kind=kind):
                r = self.repo()
                a = self.archive(kind, {"package/index.js": ("k = %s\n" % KEYS["github-token"]).encode()})
                self.assertBlocks(r.run("scan-archive", a), "FAIL secrets")

    def test_home_paths_and_emails_in_an_archive_fail(self):
        r = self.repo()
        a = self.archive("tgz", {"package/a.js": ("// /Use" + "rs/bob/x and bob@gmail." + "com\n").encode()})
        proc = r.run("scan-archive", a)
        self.assertBlocks(proc, "FAIL home-paths")
        self.assertIn("FAIL emails", proc.stderr)

    def test_a_clean_archive_passes(self):
        r = self.repo()
        a = self.archive("tgz", {"package/index.js": b"console.log(1)\n", "package/README.md": b"hello\n"})
        self.assertPasses(r.run("scan-archive", a))

    def test_source_maps_and_secret_file_names_fail(self):
        r = self.repo()
        a = self.archive("zip", {"dist/app.js.map": b"{}", "config/.env": b"A=1\n", "dist/app.js": b"x\n"})
        proc = r.run("scan-archive", a)
        self.assertBlocks(proc, "FAIL source-maps")
        self.assertIn("FAIL secret-files", proc.stderr)

    def test_private_names_are_checked_for_public_repositories(self):
        r = self.repo(visibility="public")
        a = self.archive("tgz", {"package/a.md": b"the zebra" + b"foo project\n"})
        self.assertBlocks(r.run("scan-archive", a, use_names=True), "FAIL private-names")
        self.assertErrors(r.run("scan-archive", a), "private-names list is missing")

    def test_path_traversal_is_an_error(self):
        r = self.repo()
        a = self.archive("tgz", {"../escape.txt": b"x\n"})
        self.assertErrors(r.run("scan-archive", a), "unsafe path")

    def test_a_non_archive_and_a_missing_file_are_errors(self):
        r = self.repo()
        r.write("plain.txt", "x\n")
        self.assertErrors(r.run("scan-archive", r.path("plain.txt")), "not a tar or zip")
        self.assertErrors(r.run("scan-archive", r.path("nope.tgz")), "archive not found")
        self.assertErrors(r.run("scan-archive"), "needs at least one file")

    def test_one_secret_among_many_files(self):
        r = self.repo()
        members = {"package/f%d.txt" % i: b"line\n" for i in range(3000)}
        members["package/deep/hit.txt"] = ("k = %s\n" % KEYS["aws"]).encode()
        a = self.archive("tgz", members)
        self.assertBlocks(r.run("scan-archive", a), "package/deep/hit.txt")


class HistoryTests(Base):
    CHECK = "history"

    def test_a_secret_added_and_removed_is_found(self):
        r = self.repo(commit_first=True)
        r.stage("a.txt", "k = %s\n" % KEYS["openai-proj"])
        r.commit("feat: add")
        r.stage("a.txt", "clean\n")
        r.commit("fix: remove")
        self.assertBlocks(r.run("history", use_names=True), "FAIL secrets")

    def test_a_clean_history_passes(self):
        r = self.repo(commit_first=True)
        r.stage("a.txt", "fine\n")
        r.commit("feat: add")
        r.git("tag", "-a", "v0.1.0", "-m", "feat: first release")
        self.assertPasses(r.run("history", use_names=True))

    def test_a_personal_author_address_in_history_is_found(self):
        r = self.repo(commit_first=True)
        r.stage("a.txt", "fine\n")
        r.git("-c", "user.email=bob@gmail." + "com", "commit", "-q", "-m", "feat: add")
        self.assertBlocks(r.run("history", use_names=True), "FAIL author-emails")

    def test_a_private_name_in_an_old_message_is_found(self):
        r = self.repo(commit_first=True)
        r.stage("a.txt", "fine\n")
        r.commit("feat: add the zebra" + "foo module")
        self.assertBlocks(r.run("history", use_names=True), "FAIL private-names")

    def test_a_private_name_in_a_tag_message_is_found(self):
        r = self.repo(commit_first=True)
        r.git("tag", "-a", "v0.1.0", "-m", "release of zebra" + "foo")
        self.assertBlocks(r.run("history", use_names=True), "FAIL private-names")

    def test_a_secret_in_a_commit_on_another_branch_is_found(self):
        r = self.repo(commit_first=True)
        r.git("checkout", "-q", "-b", "side")
        r.stage("a.txt", "k = %s\n" % KEYS["stripe-live"])
        r.commit("feat: side")
        r.git("checkout", "-q", "main")
        self.assertBlocks(r.run("history", use_names=True), "FAIL secrets")

    def test_a_missing_names_list_is_an_error_even_for_a_private_repository(self):
        r = self.repo(visibility="private", commit_first=True)
        self.assertErrors(r.run("history"), "private-names list is missing")
