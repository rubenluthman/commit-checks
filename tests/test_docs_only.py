import os

from support import Base

CONF = {"paths": ["*.md"], "except": ["platforms/*", "docs/DESIGN.md"]}


class DocsOnlyTests(Base):
    CHECK = "docs-only"

    def proj(self, conf=CONF):
        r = self.repo(commit_first=False, settings_extra={"docs_only": conf} if conf is not None else None)
        r.write("README.md", "a\n")
        r.write("src/a.swift", "let a = 1\n")
        r.write("docs/DESIGN.md", "d\n")
        r.write("src/X.docc/X.md", "x\n")
        r.write("sub/README.md", "s\n")
        r.git("add", "-A")
        r.commit("chore: start")
        return r

    def mode(self, r, env=None):
        e = {"CI": ""}
        e.update(env or {})
        return r.run("docs-only", env=e)

    def test_readme_only_exits_0(self):
        r = self.proj()
        r.write("README.md", "b\n")
        p = self.mode(r)
        self.assertPasses(p)
        self.assertIn("docs-only: 1 changed file, all docs", p.stdout)

    def test_code_exits_1(self):
        r = self.proj()
        r.write("src/a.swift", "let a = 2\n")
        r.write("README.md", "b\n")
        p = self.mode(r)
        self.assertBlocks(p)
        self.assertIn("code: src/a.swift", p.stdout)

    def test_more_count(self):
        r = self.proj()
        for n in "abc":
            r.write("src/%s.swift" % n, "x\n")
        self.assertIn("(and 2 more)", self.mode(r).stdout)

    def test_except_and_docc(self):
        r = self.proj()
        r.write("docs/DESIGN.md", "e\n")
        self.assertBlocks(self.mode(r))
        r2 = self.proj({"paths": ["*.md"], "except": ["*.docc/*"]})
        r2.write("src/X.docc/X.md", "y\n")
        self.assertBlocks(self.mode(r2))

    def test_no_pattern_match(self):
        r = self.proj()
        r.write("notes.txt", "n\n")
        self.assertBlocks(self.mode(r))

    def test_basename_only_does_not_match(self):
        r = self.proj({"paths": ["README.md"]})
        r.write("sub/README.md", "t\n")
        self.assertBlocks(self.mode(r))
        r.git("checkout", "--", "sub/README.md")
        r.write("README.md", "t\n")
        self.assertPasses(self.mode(r))

    def test_untracked_code_beside_docs(self):
        r = self.proj()
        r.write("README.md", "b\n")
        r.write("src/new.swift", "x\n")
        self.assertBlocks(self.mode(r))

    def test_ignored_untracked_is_skipped(self):
        r = self.proj()
        r.write("README.md", "b\n")
        r.write("secret.key", "x\n")
        self.assertPasses(self.mode(r))

    def test_unstaged_code_beside_staged_doc(self):
        r = self.proj()
        r.stage("README.md", "b\n")
        r.write("src/a.swift", "let a = 3\n")
        self.assertBlocks(self.mode(r))

    def test_staged_only_and_unstaged_only(self):
        r = self.proj()
        r.stage("README.md", "b\n")
        self.assertPasses(self.mode(r))
        r2 = self.proj()
        r2.write("README.md", "b\n")
        self.assertPasses(self.mode(r2))

    def test_rename_code_to_docs(self):
        r = self.proj()
        r.git("mv", "src/a.swift", "docs/a.md")
        self.assertBlocks(self.mode(r))

    def test_deleted_code(self):
        r = self.proj()
        os.remove(r.path("src/a.swift"))
        self.assertBlocks(self.mode(r))

    def test_empty_change_set(self):
        self.assertBlocks(self.mode(self.proj()))

    def test_ci_set(self):
        r = self.proj()
        r.write("README.md", "b\n")
        self.assertBlocks(self.mode(r, env={"CI": "1"}))

    def test_no_setting(self):
        r = self.proj(None)
        r.write("README.md", "b\n")
        p = self.mode(r)
        self.assertBlocks(p)
        self.assertIn("no docs_only setting", p.stdout)

    def test_no_commits_yet(self):
        r = self.repo(settings_extra={"docs_only": CONF})
        r.write("README.md", "a\n")
        r.git("add", "README.md")
        self.assertBlocks(self.mode(r))

    def test_bad_settings_exit_2(self):
        for bad in ("x", [], {}, {"paths": []}, {"paths": ["*"]}, {"paths": [""]}, {"paths": "*.md"},
                    {"paths": ["*.md"], "except": "x"}, {"paths": ["*.md"], "except": [""]},
                    {"paths": ["*.md"], "other": 1}):
            r = self.proj(bad)
            r.write("README.md", "b\n")
            self.assertErrors(self.mode(r))
