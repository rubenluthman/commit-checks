import json
import os

from support import Base


def changelog(top="1.2.3"):
    return "# Changelog\n\n## [Unreleased]\n\n## [%s] - 2026-01-01\n\n- change\n" % top


class VersionTests(Base):
    CHECK = "versions"

    def proj(self, source, version="1.2.3", top="1.2.3", commit=True):
        r = self.repo()
        r.stage("CHANGELOG.md", changelog(top))
        r.settings(release={"source": source, "code": ["*.swift", "*.json"]})
        if commit:
            r.git("add", "-A")
        return r

    def test_package_json_matches_changelog(self):
        r = self.proj({"type": "package-json", "path": "package.json"})
        r.stage("package.json", json.dumps({"version": "1.2.3"}))
        self.assertPasses(r.run("verify"))
        self.assertPasses(r.run("pre-commit"))

    def test_changelog_mismatch_fails(self):
        r = self.proj({"type": "package-json", "path": "package.json"}, top="1.2.2")
        r.stage("package.json", json.dumps({"version": "1.2.3"}))
        self.assertBlocks(r.run("verify"), "does not match the version 1.2.3")
        self.assertBlocks(r.run("pre-commit"), "FAIL versions")

    def test_missing_changelog_fails(self):
        r = self.repo()
        r.settings(release={"source": {"type": "package-json", "path": "package.json"}})
        r.stage("package.json", json.dumps({"version": "1.2.3"}))
        self.assertBlocks(r.run("verify"), "CHANGELOG is missing")

    def test_untracked_or_unreadable_sources_are_errors(self):
        r = self.proj({"type": "package-json", "path": "package.json"})
        self.assertErrors(r.run("verify"), "not tracked")
        r.stage("package.json", "{not json")
        self.assertErrors(r.run("verify"), "cannot read the version")
        r2 = self.proj({"type": "nonsense", "path": "x.txt"})
        r2.stage("x.txt", "1.2.3\n")
        self.assertErrors(r2.run("verify"), "release.source.type")
        r3 = self.proj({"type": "regex", "path": "x.txt", "pattern": "version=(\\d+)"})
        r3.stage("x.txt", "nothing\n")
        self.assertErrors(r3.run("verify"), "no version found")

    def test_other_sources(self):
        cases = [
            ({"type": "xcode", "path": "P.xcodeproj/project.pbxproj"}, "P.xcodeproj/project.pbxproj",
             "\t\t\t\tMARKETING_VERSION = 1.2.3;\n\t\t\t\tMARKETING_VERSION = 1.2.3;\n"),
            ({"type": "pyproject", "path": "pyproject.toml"}, "pyproject.toml", '[project]\nversion = "1.2.3"\n'),
            ({"type": "plist", "path": "Info.plist"}, "Info.plist", "<key>CFBundleShortVersionString</key>\n<string>1.2.3</string>\n"),
            ({"type": "regex", "path": "README.md", "pattern": "Version: (\\S+)"}, "README.md", "Version: 1.2.3\n"),
        ]
        for source, path, text in cases:
            with self.subTest(type=source["type"]):
                r = self.proj(source)
                r.stage(path, text)
                self.assertPasses(r.run("verify"))
                bad = self.proj(source, top="9.9.9")
                bad.stage(path, text)
                self.assertBlocks(bad.run("verify"), "FAIL versions")

    def test_two_different_versions_in_one_source_fail(self):
        r = self.proj({"type": "xcode", "path": "p.pbxproj"})
        r.stage("p.pbxproj", "MARKETING_VERSION = 1.2.3;\nMARKETING_VERSION = 1.2.4;\n")
        self.assertBlocks(r.run("verify"), "more than one value")

    def test_a_repository_without_release_settings_skips_the_check(self):
        r = self.repo()
        r.stage("a.txt", "x\n")
        self.assertPasses(r.run("verify"))

    def _released(self, tag="v1.2.3"):
        r = self.proj({"type": "package-json", "path": "package.json"})
        r.stage("package.json", json.dumps({"version": "1.2.3"}))
        r.commit("chore: release")
        r.git("tag", "-a", tag, "-m", "release")
        return r

    def test_code_changes_under_a_released_tag_fail(self):
        for tag in ("v1.2.3", "1.2.3"):
            with self.subTest(tag=tag):
                r = self._released(tag)
                r.stage("Main.swift", "print(1)\n")
                self.assertBlocks(r.run("pre-commit"), "code changed under the released tag")
                self.assertBlocks(r.run("verify"), "FAIL versions")

    def test_docs_only_changes_under_a_released_tag_pass(self):
        r = self._released()
        r.stage("notes.txt", "x\n")
        self.assertPasses(r.run("pre-commit"))

    def test_the_clean_tagged_commit_passes(self):
        r = self._released()
        self.assertPasses(r.run("verify"))

    def test_code_committed_since_the_tag_is_still_found(self):
        r = self._released()
        r.stage("Main.swift", "print(1)\n")
        r.commit("fix: change")
        self.assertBlocks(r.run("verify"), "code changed under the released tag")

    def test_bumping_the_version_clears_it(self):
        r = self._released()
        r.stage("Main.swift", "print(1)\n")
        r.stage("package.json", json.dumps({"version": "1.2.4"}))
        r.stage("CHANGELOG.md", changelog("1.2.4"))
        self.assertPasses(r.run("pre-commit"))


class ReleaseTagTests(Base):
    CHECK = "release-tags"

    def test_lightweight_release_tags_fail(self):
        r = self.repo(commit_first=True)
        r.git("tag", "v1.0.0")
        self.assertBlocks(r.run("verify"), "FAIL release-tags: v1.0.0")

    def test_annotated_and_other_tags_pass(self):
        r = self.repo(commit_first=True)
        r.git("tag", "-a", "v1.0.0", "-m", "release")
        r.git("tag", "-a", "2.0.0", "-m", "release")
        r.git("tag", "backup-before-rebase")
        self.assertPasses(r.run("verify"))

    def test_an_old_lightweight_tag_can_be_allowed(self):
        r = self.repo(commit_first=True)
        r.git("tag", "v0.1.0")
        r.settings(allow=[{"rule": "release-tags", "path": "v0.1.0", "reason": "created before the annotation rule"}])
        self.assertPasses(r.run("verify"))


class CommitMessageTests(Base):
    CHECK = "conventional-commits"

    def msg(self, r, text):
        r.write("MSG", text)
        return r.run("commit-msg", r.path("MSG"))

    def test_good_subjects_pass(self):
        r = self.repo()
        for s in ("feat: add a thing", "fix(editor): keep the cursor on undo", "refactor!: drop the old API", "docs(adr): add 0017",
                  "chore(release): 1.2.3", 'Revert "feat: x"', "Merge branch 'a' into b", "fixup! fix: x", "revert: undo y"):
            with self.subTest(subject=s):
                self.assertPasses(self.msg(r, s + "\n\nBody.\n"))

    def test_bad_subjects_fail(self):
        r = self.repo()
        for s in ("added a thing", "Fix: capital", "feat:no space", "feat(): empty scope", "feat add", "wip", ""):
            with self.subTest(subject=s):
                self.assertBlocks(self.msg(r, s + "\n"), "FAIL conventional-commits")

    def test_a_subject_longer_than_the_repository_limit_fails(self):
        r = self.repo()
        long = "feat: " + "x" * 70
        self.assertPasses(self.msg(r, long + "\n"))
        r.settings(subject_max=72)
        self.assertBlocks(self.msg(r, long + "\n"), "longer than 72")
        self.assertPasses(self.msg(r, "feat: short\n"))

    def test_comment_lines_are_ignored(self):
        r = self.repo()
        self.assertPasses(self.msg(r, "# Please enter\nfix: real subject\n"))

    def test_a_repository_can_switch_it_off(self):
        r = self.repo()
        r.settings(conventional_commits=False)
        self.assertPasses(self.msg(r, "whatever\n"))

    def test_pushed_subjects_are_checked(self):
        r = self.repo(commit_first=True)
        base = r.git("rev-parse", "HEAD").stdout.strip()
        r.stage("a.txt", "x\n")
        new = r.commit("not conventional")
        proc = r.run("pre-push", stdin="refs/heads/main %s refs/heads/main %s\n" % (new, base))
        self.assertBlocks(proc, "FAIL conventional-commits")
        r.stage("b.txt", "y\n")
        ok = r.commit("fix: fine")
        proc = r.run("pre-push", stdin="refs/heads/main %s refs/heads/main %s\n" % (ok, new))
        self.assertPasses(proc)
