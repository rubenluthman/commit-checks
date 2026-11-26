import os

from support import Base, PKG

HOOKS = ("pre-commit", "commit-msg", "pre-push")
MARKER = "# --- repository checks ---"


def rule(glob=None, trigger="glob"):
    text = "---\ntrigger: %s\n" % trigger
    if glob is not None:
        text += 'glob: "%s"\n' % glob
    return text + "---\nBody mentions trigger: glob and glob: nothing.\n"


class RuleGlobTests(Base):
    CHECK = "rule-globs"

    def test_an_orphaned_glob_fails(self):
        r = self.repo()
        r.stage(".agents/rules/swift.md", rule("Sources/**/*.swift"))
        r.stage("README.md", "x\n")
        self.assertBlocks(r.run("verify"), "matches no tracked file")
        self.assertBlocks(r.run("pre-commit"), "rule-globs")

    def test_a_matching_glob_passes(self):
        r = self.repo()
        r.stage(".agents/rules/swift.md", rule("*.swift"))
        r.stage("Sources/App/View.swift", "x\n")
        self.assertPasses(r.run("verify"))

    def test_a_brace_glob_that_matches_passes(self):
        r = self.repo()
        r.stage(".agents/rules/web.md", rule("src/*.{ts,tsx}"))
        r.stage("src/App.tsx", "x\n")
        self.assertPasses(r.run("verify"))

    def test_a_brace_glob_where_no_option_matches_fails(self):
        r = self.repo()
        r.stage(".agents/rules/web.md", rule("src/*.{ts,tsx}"))
        r.stage("src/App.js", "x\n")
        self.assertBlocks(r.run("verify"), "web.md")

    def test_a_rule_without_a_glob_trigger_is_ignored(self):
        r = self.repo()
        r.stage(".agents/rules/always.md", rule("nothing/*.zzz", trigger="always_on"))
        self.assertPasses(r.run("verify"))

    def test_the_word_glob_in_the_body_does_not_make_a_rule_scoped(self):
        r = self.repo()
        r.stage(".agents/rules/note.md", "---\ntrigger: always_on\n---\ntrigger: glob\nglob: nothing/*.zzz\n")
        self.assertPasses(r.run("verify"))

    def test_trigger_glob_without_a_pattern_fails(self):
        r = self.repo()
        r.stage(".agents/rules/bad.md", rule(None))
        self.assertBlocks(r.run("verify"), "no glob pattern")

    def test_no_rules_folder_means_no_check(self):
        r = self.repo()
        r.stage("a.txt", "x\n")
        self.assertPasses(r.run("verify"))


class SwiftFilenameTests(Base):
    CHECK = "swift-filenames"

    def test_a_lower_case_name_fails(self):
        r = self.repo()
        r.stage("App/helper.swift", "x\n")
        self.assertBlocks(r.run("verify"), "PascalCase")
        self.assertBlocks(r.run("pre-commit"), "helper.swift")

    def test_pascal_case_and_extension_files_pass(self):
        r = self.repo()
        r.stage("App/Helper.swift", "x\n")
        r.stage("App/String+Trim.swift", "x\n")
        self.assertPasses(r.run("verify"))

    def test_package_and_main_are_excepted(self):
        r = self.repo()
        r.stage("Package.swift", "x\n")
        r.stage("App/main.swift", "x\n")
        self.assertPasses(r.run("verify"))

    def test_an_allow_entry_with_a_reason_passes(self):
        r = self.repo(settings_extra={"allow": [{"rule": "swift-filenames", "path": "Gen/*", "reason": "generated"}]})
        r.stage("Gen/thing.swift", "x\n")
        self.assertPasses(r.run("verify"))

    def test_no_swift_files_means_no_check(self):
        r = self.repo()
        r.stage("a.txt", "x\n")
        self.assertPasses(r.run("verify"))


class BinaryMediaTests(Base):
    CHECK = "binary-media"

    def test_a_staged_video_fails(self):
        r = self.repo(settings_extra={"binary_allow": []})
        r.stage("clip.mp4", "x")
        self.assertBlocks(r.run("pre-commit"), "clip.mp4")
        self.assertBlocks(r.run("verify"), "binary-media")

    def test_an_allowed_image_passes(self):
        r = self.repo(settings_extra={"binary_allow": ["static/*.png"]})
        r.stage("static/icon.png", "x")
        self.assertPasses(r.run("pre-commit"))

    def test_an_image_outside_the_allow_list_fails(self):
        r = self.repo(settings_extra={"binary_allow": ["static/*.png"]})
        r.stage("other/icon.PNG", "x")
        self.assertBlocks(r.run("pre-commit"), "icon.PNG")

    def test_an_archive_fails(self):
        r = self.repo(settings_extra={"binary_allow": []})
        r.stage("out.tar.gz", "x")
        self.assertBlocks(r.run("pre-commit"), "out.tar.gz")

    def test_a_repository_without_the_key_is_not_checked(self):
        r = self.repo()
        r.stage("clip.mp4", "x")
        self.assertPasses(r.run("pre-commit"))

    def test_a_renamed_tracked_file_is_not_new_media(self):
        r = self.repo(settings_extra={"binary_allow": []}, commit_first=False)
        r.settings(binary_allow=None)
        r.write("a.png", "x" * 200)
        r.git("add", "a.png")
        r.commit()
        r.settings(binary_allow=[])
        r.git("mv", "a.png", "b.png")
        self.assertPasses(r.run("pre-commit"))

    def test_a_bad_key_is_an_error(self):
        r = self.repo(settings_extra={"binary_allow": "*.png"})
        r.stage("a.txt", "x\n")
        self.assertErrors(r.run("verify"), "binary_allow")


class DashesPlainTests(Base):
    CHECK = "dashes-plain"
    DASH = chr(0x2014)

    def test_an_em_dash_in_a_script_fails(self):
        r = self.repo()
        r.stage("scripts/a.sh", "# a %s b\n" % self.DASH)
        self.assertBlocks(r.run("verify"), "spaced en dash")
        self.assertBlocks(r.run("pre-commit"), "dashes-plain")

    def test_each_file_type_is_read(self):
        for name in ("a.yml", "a.yaml", "a.toml", "a.svg"):
            with self.subTest(name=name):
                r = self.repo()
                r.stage(name, "k: a %s b\n" % self.DASH)
                self.assertBlocks(r.run("verify"), name)

    def test_an_en_dash_passes(self):
        r = self.repo()
        r.stage("a.sh", "# a %s b\n" % chr(0x2013))
        self.assertPasses(r.run("verify"))

    def test_private_notes_and_other_types_are_skipped(self):
        r = self.repo()
        r.stage("docs/private/a.yml", "k: a %s b\n" % self.DASH)
        r.stage("notes.txt", "a %s b\n" % self.DASH)
        self.assertPasses(r.run("verify"))


class HookDriftTests(Base):
    CHECK = "hooks-installed"

    def _install(self, r, tweak=None):
        with open(os.path.join(PKG, "templates", "loader.py"), encoding="utf-8") as fh:
            r.write("scripts/commit-checks", fh.read())
        for hook in HOOKS:
            with open(os.path.join(PKG, "templates", "githooks", hook), encoding="utf-8") as fh:
                text = fh.read()
            text = tweak(hook, text) if tweak else text
            r.write(".githooks/" + hook, text)
            os.chmod(r.path(".githooks/" + hook), 0o755)
        os.chmod(r.path("scripts/commit-checks"), 0o755)
        r.git("config", "core.hooksPath", ".githooks")
        r.git("add", "scripts/commit-checks")

    def _repo(self):
        return self.repo(settings_extra={"self": False})

    def run_local(self, r):
        return r.run("verify", env={"CI": ""})

    def test_a_hook_that_matches_the_template_passes(self):
        r = self._repo()
        self._install(r)
        self.assertPasses(self.run_local(r))

    def test_own_lines_below_the_marker_pass(self):
        r = self._repo()
        self._install(r, lambda h, t: t + "echo own check\n")
        self.assertPasses(self.run_local(r))

    def test_a_drifted_header_fails(self):
        r = self._repo()
        self._install(r, lambda h, t: t.replace("exit 1", "exit 2", 1) if h == "pre-commit" else t)
        proc = self.run_local(r)
        self.assertBlocks(proc, ".githooks/pre-commit differs")
        self.assertNotIn(".githooks/pre-push differs", proc.stderr)

    def test_a_hook_without_the_marker_fails_and_says_how_to_fix_it(self):
        r = self._repo()
        self._install(r, lambda h, t: t.replace(MARKER + "\n", "") if h == "commit-msg" else t)
        proc = self.run_local(r)
        self.assertBlocks(proc, ".githooks/commit-msg has no '%s' line" % MARKER)
        self.assertIn("copy templates/githooks/commit-msg", proc.stderr)

    def test_the_package_s_own_hooks_are_not_compared(self):
        r = self.repo()  # settings say "self": true
        self._install(r, lambda h, t: "#!/bin/sh\n# commit-checks\n")
        self.assertPasses(self.run_local(r))
