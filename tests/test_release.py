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

    def _released_with(self, code, docs_only, path, *more):
        r = self._released()
        settings = {"release": {"source": {"type": "package-json", "path": "package.json"}, "code": code}}
        if docs_only is not None:
            settings["docs_only"] = docs_only
        r.settings(**settings)
        r.git("add", ".commit-checks.json")
        r.commit("chore: settings")
        r.git("tag", "-d", "v1.2.3")
        r.git("tag", "-a", "v1.2.3", "-m", "release")
        for p in (path,) + more:
            r.stage(p, "x\n")
        return r

    def test_docs_under_a_code_glob_need_no_bump(self):
        r = self._released_with(["tools/*"], {"paths": ["*.md"]}, "tools/x/README.md")
        self.assertPasses(r.run("pre-commit"))
        self.assertPasses(r.run("verify"))

    def test_without_docs_only_the_same_docs_need_a_bump(self):
        r = self._released_with(["tools/*"], None, "tools/x/README.md")
        self.assertBlocks(r.run("pre-commit"), "code changed under the released tag")

    def test_a_doc_in_except_counts_as_code(self):
        r = self._released_with(["docs/*"], {"paths": ["*.md"], "except": ["docs/DESIGN.md"]}, "docs/DESIGN.md")
        self.assertBlocks(r.run("pre-commit"), "code changed under the released tag")

    def test_code_under_the_glob_needs_a_bump_despite_docs_only(self):
        r = self._released_with(["tools/*"], {"paths": ["*.md"]}, "tools/x/main.js")
        self.assertBlocks(r.run("pre-commit"), "code changed under the released tag")

    def test_code_and_docs_together_need_a_bump(self):
        r = self._released_with(["tools/*"], {"paths": ["*.md"]}, "tools/x/main.js", "tools/x/README.md")
        self.assertBlocks(r.run("pre-commit"), "code changed under the released tag")

    def test_the_clean_tagged_commit_passes(self):
        r = self._released()
        self.assertPasses(r.run("verify"))

    def test_code_committed_since_the_tag_is_still_found(self):
        r = self._released()
        r.stage("Main.swift", "print(1)\n")
        r.commit("fix: change")
        self.assertBlocks(r.run("verify"), "code changed under the released tag")

    def test_a_changed_version_must_not_reuse_a_tag(self):
        r = self.proj({"type": "package-json", "path": "package.json"}, top="1.2.2")
        r.stage("package.json", json.dumps({"version": "1.2.2"}))
        r.commit("chore: start")
        r.git("tag", "-a", "v1.2.3", "-m", "already released elsewhere")
        r.stage("package.json", json.dumps({"version": "1.2.3"}))
        r.stage("CHANGELOG.md", changelog("1.2.3"))
        self.assertBlocks(r.run("pre-commit"), "already released as tag v1.2.3")
        r.stage("package.json", json.dumps({"version": "1.2.4"}))
        r.stage("CHANGELOG.md", changelog("1.2.4"))
        self.assertPasses(r.run("pre-commit"))

    def test_bumping_the_version_clears_it(self):
        r = self._released()
        r.stage("Main.swift", "print(1)\n")
        r.stage("package.json", json.dumps({"version": "1.2.4"}))
        r.stage("CHANGELOG.md", changelog("1.2.4"))
        self.assertPasses(r.run("pre-commit"))


FONT = (r"(?s)<key>versionMajor</key>\s*<integer>(\d+)</integer>.*?"
        r"<key>versionMinor</key>\s*<integer>(\d+)</integer>")


def font(major, minor):
    return ("<dict><key>versionMajor</key>\n<integer>%s</integer>\n"
            "<key>versionMinor</key>\n<integer>%s</integer></dict>\n" % (major, minor))


class BuildNumberTests(Base):
    CHECK = "versions"

    def released(self, build, text, version="1.4.1", tag="v1.4.1"):
        r = self.repo()
        r.settings(release={"source": {"type": "package-json", "path": "package.json"}, "build": build})
        r.stage("package.json", json.dumps({"version": version}))
        r.stage("CHANGELOG.md", changelog(version))
        r.stage(build["path"], text)
        r.commit("chore: release")
        r.git("tag", "-a", tag, "-m", "release")
        return r

    def bump(self, r, build_text, version="1.4.1"):
        r.stage("package.json", json.dumps({"version": version}))
        r.stage("CHANGELOG.md", changelog(version))
        r.stage("b.txt", build_text)

    def setup_regex(self, old, tag="v1.4.1"):
        return self.released({"type": "regex", "path": "b.txt", "pattern": r"build=(\S+)"}, "build=%s\n" % old, tag=tag)

    def test_a_higher_build_passes(self):
        r = self.setup_regex("1.11")
        self.bump(r, "build=1.12\n", "1.4.2")
        self.assertPasses(r.run("verify"))

    def test_an_equal_build_with_an_unchanged_version_passes(self):
        r = self.setup_regex("12")
        self.bump(r, "build=12\n")
        self.assertPasses(r.run("verify"))

    def test_an_equal_build_with_a_new_version_fails(self):
        r = self.setup_regex("1.12")
        self.bump(r, "build=1.12\n", "1.4.2")
        self.assertBlocks(r.run("verify"), "build number (1, 12) is not higher than (1, 12) at v1.4.1; raise it for 1.4.2")

    def test_a_lower_build_fails(self):
        r = self.setup_regex("1.12")
        self.bump(r, "build=1.3\n", "1.4.2")
        self.assertBlocks(r.run("verify"), "build number (1, 3) is lower than (1, 12) at v1.4.1")

    def test_no_tag_yet_passes(self):
        r = self.repo()
        r.settings(release={"source": {"type": "package-json", "path": "package.json"},
                            "build": {"type": "regex", "path": "b.txt", "pattern": r"build=(\S+)"}})
        r.stage("package.json", json.dumps({"version": "1.4.1"}))
        r.stage("CHANGELOG.md", changelog("1.4.1"))
        r.stage("b.txt", "build=1\n")
        self.assertPasses(r.run("verify"))

    def test_a_file_missing_at_the_tag_passes(self):
        r = self.setup_regex("1")
        r.git("rm", "-q", "-f", "b.txt")
        r.commit("chore: drop")
        r.git("tag", "-a", "v1.4.5", "-m", "release")
        self.bump(r, "build=1\n", "1.4.6")
        self.assertPasses(r.run("verify"))

    def test_several_groups_make_a_tuple(self):
        build = {"type": "regex", "path": "fontinfo.plist", "pattern": FONT}
        r = self.released(build, font(1, 999))
        r.stage("package.json", json.dumps({"version": "1.4.2"}))
        r.stage("CHANGELOG.md", changelog("1.4.2"))
        r.stage("fontinfo.plist", font(2, 0))
        self.assertPasses(r.run("verify"))
        r.stage("fontinfo.plist", font(1, 998))
        self.assertBlocks(r.run("verify"), "(1, 998) is lower than (1, 999)")

    def test_two_values_in_one_file_fail(self):
        r = self.setup_regex("1")
        self.bump(r, "build=1\nbuild=2\n")
        self.assertBlocks(r.run("verify"), "build number holds more than one value")

    def test_a_non_numeric_build_is_an_error(self):
        r = self.setup_regex("1")
        self.bump(r, "build=abc\n")
        self.assertErrors(r.run("verify"), "not made of digits")

    def test_a_bad_setting_is_an_error(self):
        r = self.released({"type": "nonsense", "path": "b.txt"}, "x\n")
        self.assertErrors(r.run("verify"), "release.build.type")

    def test_presets_read_their_samples(self):
        cases = [
            ("xcode-build", "p.pbxproj", "\t\tCURRENT_PROJECT_VERSION = %s;\n\t\tCURRENT_PROJECT_VERSION = %s;\n"),
            ("xcode-build", "project.xcproj", '{"CURRENT_PROJECT_VERSION": "%s", "x": {"CURRENT_PROJECT_VERSION": "%s"}}\n'),
            ("plist-build", "Info.plist", "<key>CFBundleVersion</key>\n<string>%s</string>\n<key>CFBundleVersion</key><string>%s</string>\n"),
        ]
        for kind, path, tpl in cases:
            with self.subTest(type=kind, path=path):
                build = {"type": kind, "path": path}
                r = self.released(build, tpl % (92, 92))
                r.stage("package.json", json.dumps({"version": "1.4.2"}))
                r.stage("CHANGELOG.md", changelog("1.4.2"))
                r.stage(path, tpl % (93, 93))
                self.assertPasses(r.run("verify"))
                r.stage(path, tpl % (92, 92))
                self.assertBlocks(r.run("verify"), "build number (92) is not higher than (92) at v1.4.1")


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

    def test_annotated_tags_are_required_only_from_a_version_on(self):
        r = self.repo(commit_first=True)
        r.git("tag", "v0.1.4")
        r.git("tag", "v0.1.9")
        r.settings(annotated_tags_from="0.1.10")
        self.assertPasses(r.run("verify"))
        r.git("tag", "v0.1.10")
        self.assertBlocks(r.run("verify"), "FAIL release-tags: v0.1.10")
        r.settings(annotated_tags_from="nonsense")
        self.assertErrors(r.run("verify"), "annotated_tags_from")

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
