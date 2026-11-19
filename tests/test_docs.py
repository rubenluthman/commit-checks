import os

from support import Base


def record(n, status="Active", body="We do this.", title=None):
    title = title or "Decision %d" % n
    return ("# %s\nStatus: %s · 2026-01-01\n\n## Decision\n\n%s\n\n## Why\n\nBecause.\n\n## What we gave up\n\n"
            "Something.\n\n## Revisit if\n\nThings change.\n" % (title, status, body))


def fname(n):
    return "%04d-decision-%d.md" % (n, n)


def index_row(n, status="Active"):
    return "| [%04d](%s) | Decision %d | %s |" % (n, fname(n), n, status)


class LinkTests(Base):
    CHECK = "links"

    def md(self, text, extra=None):
        r = self.repo()
        r.stage("docs/a.md", text)
        r.stage("docs/b.md", "target\n")
        r.stage("top.md", "top\n")
        for name, body in (extra or {}).items():
            r.stage(name, body)
        return r

    def test_broken_forms_are_blocked(self):
        cases = ["[x](nope.md)", "![img](missing.png)", "[x](<nope.md>)", "[x]: nope.md", "[x](nope.md \"title\")",
                 "[x](file:///somewhere/else.md)", "[x](../../outside.md)", "[x](B.md)", "[x](../Top.md)"]
        for text in cases:
            with self.subTest(text=text):
                r = self.md(text + "\n")
                self.assertBlocks(r.run("verify"), "FAIL links: docs/a.md")
                self.assertBlocks(r.run("pre-commit"), "FAIL links")

    def test_good_forms_pass(self):
        text = "\n".join([
            "[a](b.md) [b](b.md#anchor) [c](../top.md) [d](/top.md) [e](https://example.com/x) [f](mailto:a@example.com)",
            "[g](#section) [h](./) [i](..) [j](b.md \"title\") [k](<b.md>) [l](b.md?x=1) [m](../top.md:42) [n](my%20file.md)",
            "[ref]: b.md", "`[code](nope.md)`", "```", "[fenced](nope.md)", "```", "~~~", "[tilde](nope.md)", "~~~", ""])
        r = self.md(text, {"docs/my file.md": "x\n"})
        self.assertPasses(r.run("verify"))
        self.assertPasses(r.run("pre-commit"))

    def test_odd_file_names_resolve(self):
        r = self.repo()
        r.stage("docs/åäö fil.md", "x\n")
        r.stage("docs/a.md", "[x](%C3%A5%C3%A4%C3%B6%20fil.md)\n")
        self.assertPasses(r.run("verify"))

    def test_a_link_to_an_untracked_file_fails(self):
        r = self.md("[x](local.md)\n")
        r.write("docs/local.md", "on disk only\n")
        self.assertBlocks(r.run("verify"), "FAIL links")

    def test_the_staged_content_is_what_counts(self):
        r = self.md("[x](nope.md)\n")
        r.write("docs/a.md", "clean now\n")  # unstaged fix
        self.assertBlocks(r.run("verify"), "FAIL links")
        r2 = self.md("clean\n")
        r2.write("docs/a.md", "[x](nope.md)\n")  # unstaged breakage
        self.assertPasses(r2.run("verify"))

    def test_excluded_paths_are_skipped(self):
        r = self.md("[x](nope.md)\n")
        r.settings(links_exclude=["docs/*"])
        self.assertPasses(r.run("verify"))

    def test_an_allowance_with_a_reason_covers_a_link(self):
        r = self.md("[x](nope.md)\n")
        r.settings(allow=[{"rule": "links", "path": "docs/a.md", "reason": "generated at build time"}])
        self.assertPasses(r.run("verify"))


class AdrTests(Base):
    CHECK = "adr-format"

    def adrs(self, count, **kw):
        r = self.repo(**kw)
        for n in range(1, count + 1):
            r.stage("docs/adr/" + fname(n), record(n))
        self.index(r, count)
        return r

    def index(self, r, count, statuses=None):
        rows = [index_row(n, (statuses or {}).get(n, "Active")) for n in range(1, count + 1)]
        r.stage("docs/adr/README.md", "# Index\n\n" + "\n".join(rows) + "\n")

    def assertAdr(self, r, text):
        self.assertBlocks(r.run("verify"), text)
        self.assertBlocks(r.run("pre-commit"), text)

    def test_a_clean_repository_passes(self):
        r = self.adrs(3)
        self.assertPasses(r.run("verify"))
        self.assertPasses(r.run("pre-commit"))

    def test_a_repository_without_records_passes(self):
        self.assertPasses(self.repo().run("verify"))

    def test_a_record_edited_in_place_passes(self):
        r = self.adrs(2)
        r.stage("docs/adr/" + fname(1), record(1, body="We now do something else."))
        self.assertPasses(r.run("verify"))

    def test_retiring_a_record_passes_once_the_index_agrees(self):
        r = self.adrs(2)
        r.stage("docs/adr/" + fname(2), record(2, status="Retired"))
        self.index(r, 2, {2: "Retired"})
        self.assertPasses(r.run("verify"))

    def test_a_stale_index_status_fails(self):
        r = self.adrs(2)
        r.stage("docs/adr/" + fname(2), record(2, status="Retired"))
        self.assertAdr(r, "ADR-0002 row differs")

    def test_a_missing_index_row_fails(self):
        r = self.adrs(2)
        self.index(r, 1)
        self.assertAdr(r, "ADR-0002 is missing from the index")

    def test_an_index_row_without_a_record_fails(self):
        r = self.adrs(2)
        r.git("rm", "-q", "-f", "docs/adr/" + fname(2))
        self.assertAdr(r, "ADR-0002 has no matching record")

    def test_deleting_a_replaced_record_passes_once_the_index_agrees(self):
        r = self.adrs(3)
        r.git("rm", "-q", "-f", "docs/adr/" + fname(2))
        rows = [index_row(1), index_row(3)]
        r.stage("docs/adr/README.md", "# Index\n\n" + "\n".join(rows) + "\n")
        self.assertPasses(r.run("verify"))

    def test_a_missing_heading_fails(self):
        r = self.adrs(1)
        r.stage("docs/adr/" + fname(1), record(1).replace("## Revisit if\n\nThings change.\n", ""))
        self.assertAdr(r, "headings must be exactly")

    def test_headings_out_of_order_fail(self):
        r = self.adrs(1)
        text = record(1).replace("## Why", "## Temp").replace("## What we gave up", "## Why").replace("## Temp", "## What we gave up")
        r.stage("docs/adr/" + fname(1), text)
        self.assertAdr(r, "headings must be exactly")

    def test_an_empty_section_fails(self):
        r = self.adrs(1)
        r.stage("docs/adr/" + fname(1), record(1).replace("Because.", ""))
        self.assertAdr(r, '"Why" is empty')

    def test_an_unrecognized_status_fails(self):
        r = self.adrs(1)
        r.stage("docs/adr/" + fname(1), record(1, status="Accepted"))
        self.assertAdr(r, "Status: Active | Retired")

    def test_old_frontmatter_fails(self):
        r = self.adrs(1)
        r.stage("docs/adr/" + fname(1), "---\nstatus: accepted\n---\n\n" + record(1))
        self.assertAdr(r, "frontmatter is not used")

    def test_an_em_dash_fails(self):
        r = self.adrs(1)
        r.stage("docs/adr/" + fname(1), record(1, body="Left — right."))
        self.assertAdr(r, "em dash")

    def test_a_badly_named_file_fails(self):
        r = self.adrs(1)
        r.stage("docs/adr/notes.md", "x\n")
        self.assertAdr(r, "file name must be NNNN-title.md")

    def test_duplicate_numbers_fail(self):
        r = self.adrs(1)
        r.stage("docs/adr/0001-other.md", record(1))
        self.assertAdr(r, "duplicate number 0001")

    def test_a_missing_index_fails(self):
        r = self.adrs(1)
        r.git("rm", "-q", "-f", "docs/adr/README.md")
        self.assertAdr(r, "README.md: file is missing")

    def test_the_staged_content_is_checked_not_the_working_copy(self):
        r = self.adrs(1)
        r.write("docs/adr/" + fname(1), record(1, status="Retired"))  # unstaged
        self.assertPasses(r.run("verify"))
        r.git("add", "-A")
        self.assertAdr(r, "ADR-0001 row differs")

    def test_an_exempt_old_format_record_passes(self):
        r = self.adrs(1)
        r.stage("docs/adr/0015-old.md", "# Old record\n\nFree text, no headings.\n")
        rows = [index_row(1), "| [0015](0015-old.md) | Old record | Active |"]
        r.stage("docs/adr/README.md", "# Index\n\n" + "\n".join(rows) + "\n")
        self.assertAdr(r, "headings") if False else None
        self.assertBlocks(r.run("verify"), "0015")
        r.settings(adr_exempt=["0015"])
        self.assertPasses(r.run("verify"))
