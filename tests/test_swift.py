from support import Base

PATHS = ["App/*.swift"]

VIEW = """import SwiftUI

struct SettingsView: View {
    static let selectedTabKey = "selectedTab"
    @AppStorage("showBadge") private var showBadge = true
    @AppStorage(SettingsView.selectedTabKey) private var tab = 0
    @SceneStorage("sidebarWidth") private var width = 200.0
    var body: some View { Text("hi") }
}

enum Keys {
    static let lastRun = "lastRun"
    case theme = "themeName"
}

enum CodingKeys: String, CodingKey {
    case title = "legacy_title"
}

func save(_ d: UserDefaults, _ cache: inout [String: Int]) {
    d.set(Date(), forKey: Keys.lastRun)
    d.set("x", forKey: Keys.theme.rawValue)
    cache.removeValue(forKey: "notASetting")
    let mode = d.string(forKey: "AppleInterfaceStyle")
    _ = mode
}
"""
LIVE = ["showBadge", "selectedTab", "sidebarWidth", "lastRun", "themeName"]
SYSTEM = ["AppleInterfaceStyle"]


def conf(live=None, retired=None, system=None):
    return {"settings_keys": {"paths": PATHS, "live": LIVE if live is None else live, "retired": retired or [],
                              "system": SYSTEM if system is None else system}}


class SettingsKeyTests(Base):
    CHECK = "settings-keys"

    def make(self, source=VIEW, **kw):
        r = self.repo(settings_extra=conf(**kw))
        r.stage("App/SettingsView.swift", source)
        return r

    def test_registered_keys_pass(self):
        r = self.make()
        self.assertPasses(r.run("verify"))
        self.assertPasses(r.run("pre-commit"))

    def test_no_settings_object_means_no_check(self):
        r = self.repo()
        r.stage("App/A.swift", '@AppStorage("whatever") var x = 1\n')
        self.assertPasses(r.run("verify"))

    def test_an_unregistered_literal_key_is_blocked(self):
        r = self.make(VIEW + '@AppStorage("fresh") var y = 1\n')
        self.assertBlocks(r.run("verify"), "FAIL settings-keys: App/SettingsView.swift:")
        self.assertIn("settings_keys.live", r.run("verify").stderr)

    def test_a_key_declared_as_a_constant_is_blocked(self):
        for use in ('@AppStorage(Keys.extra) var y = 1', 'UserDefaults.standard.set(1, forKey: Keys.extra)',
                    '@AppStorage(Keys.extraCase.rawValue) var y = 1'):
            with self.subTest(use=use):
                src = VIEW + 'extension Keys {\n    static let extra = "extraKey"\n    case extraCase = "extraCaseKey"\n}\n' + use + "\n"
                r = self.make(src)
                self.assertBlocks(r.run("verify"), "FAIL settings-keys: App/SettingsView.swift:")

    def test_a_constant_that_is_registered_passes(self):
        src = VIEW + 'extension Keys {\n    static let extra = "extraKey"\n}\n@AppStorage(Keys.extra) var y = 1\n'
        r = self.make(src, live=LIVE + ["extraKey"])
        self.assertPasses(r.run("verify"))

    def test_a_bare_constant_in_its_own_type_resolves(self):
        src = 'struct V {\n    static let flagKey = "flag"\n    @AppStorage(flagKey) var f = false\n}\n'
        self.assertBlocks(self.make(src, live=[], system=[]).run("verify"), "'flag'")
        self.assertPasses(self.make(src, live=["flag"], system=[]).run("verify"))

    def test_coding_keys_and_dictionaries_are_ignored(self):
        src = ('struct M: Codable {\n    enum CodingKeys: String, CodingKey { case a = "alpha" }\n'
               '    func f(_ c: KeyedEncodingContainer<CodingKeys>, _ d: inout [String: Int]) throws {\n'
               '        try c.encode(1, forKey: .a)\n        d.removeValue(forKey: "gone")\n        d.updateValue(1, forKey: "other")\n    }\n}\n')
        self.assertPasses(self.make(src, live=[], system=[]).run("verify"))

    def test_key_value_coding_on_other_objects_is_ignored(self):
        src = ('func qr(_ filter: CIFilter, _ data: Data, _ view: NSView) {\n'
               '    filter.setValue(data, forKey: "inputMessage")\n    _ = view.value(forKey: "hasKeyboardFocus")\n}\n')
        self.assertPasses(self.make(src, live=[], system=[]).run("verify"))
        for use in ('UserDefaults.standard.setValue(1, forKey: "kvc")', 'defaults.setValue(1, forKey: "kvc")',
                    '_ = sharedDefaults.value(forKey: "kvc")'):
            with self.subTest(use=use):
                self.assertBlocks(self.make(use + "\n", live=[], system=[]).run("verify"), "'kvc'")

    def test_comments_and_strings_do_not_hide_or_invent_keys(self):
        src = ('// @AppStorage("commented")\n/* UserDefaults.standard.set(1, forKey: "block") */\n'
               'let s = "@AppStorage(\\"inString\\")"\n@AppStorage("real") var r = 1\n')
        self.assertPasses(self.make(src, live=["real"], system=[]).run("verify"))

    def test_a_removed_live_key_is_blocked(self):
        r = self.make(VIEW.replace('@AppStorage("showBadge") private var showBadge = true\n', ""))
        p = r.run("verify")
        self.assertBlocks(p, "FAIL settings-keys: .commit-checks.json")
        self.assertIn("showBadge", p.stderr)
        self.assertIn("settings_keys.retired", p.stderr)

    def test_a_renamed_key_is_blocked_twice(self):
        r = self.make(VIEW.replace('"showBadge"', '"showBadges"'))
        p = r.run("verify")
        self.assertBlocks(p)
        self.assertIn("'showBadges' is not registered", p.stderr)
        self.assertIn("live key 'showBadge' is no longer", p.stderr)

    def test_a_retired_key_with_its_migration_passes(self):
        src = VIEW.replace('"showBadge"', '"showBadges"') + (
            'func migrateBadge(_ d: UserDefaults) {\n    if let v = d.object(forKey: "showBadge") { d.set(v, forKey: "showBadges") }\n'
            '    d.removeObject(forKey: "showBadge")\n}\n')
        retired = [{"key": "showBadge", "migration": "migrateBadge", "reason": "renamed"}]
        r = self.make(src, live=[k for k in LIVE if k != "showBadge"] + ["showBadges"], retired=retired)
        self.assertPasses(r.run("verify"))

    def test_a_retired_key_naming_a_missing_migration_is_blocked(self):
        src = VIEW.replace('@AppStorage("showBadge") private var showBadge = true\n', "")
        retired = [{"key": "showBadge", "migration": "migrateBadge", "reason": "dropped"}]
        r = self.make(src, live=[k for k in LIVE if k != "showBadge"], retired=retired)
        p = r.run("verify")
        self.assertBlocks(p, "FAIL settings-keys: .commit-checks.json")
        self.assertIn("migrateBadge", p.stderr)

    def test_a_migration_named_only_in_a_comment_is_blocked(self):
        src = VIEW.replace('@AppStorage("showBadge") private var showBadge = true\n', "// migrateBadge was here\n")
        retired = [{"key": "showBadge", "migration": "migrateBadge", "reason": "dropped"}]
        r = self.make(src, live=[k for k in LIVE if k != "showBadge"], retired=retired)
        self.assertBlocks(r.run("verify"), "migrateBadge")

    def test_a_key_in_two_lists_is_blocked(self):
        retired = [{"key": "lastRun", "migration": "SettingsView", "reason": "twice"}]
        self.assertBlocks(self.make(retired=retired).run("verify"), "in both live and retired")
        self.assertBlocks(self.make(system=SYSTEM + ["lastRun"]).run("verify"), "in both live and system")
        self.assertBlocks(self.make(live=LIVE + ["lastRun"]).run("verify"), "twice in live")

    def test_files_outside_paths_are_not_read(self):
        r = self.make()
        r.stage("Other/B.swift", '@AppStorage("elsewhere") var z = 1\n')
        self.assertPasses(r.run("verify"))

    def test_a_glob_that_matches_nothing_is_an_error(self):
        r = self.repo(settings_extra=conf())
        self.assertErrors(r.run("verify"), "matches no tracked file")

    def test_an_allowance_with_a_reason_covers_a_finding(self):
        r = self.make(VIEW + '@AppStorage("fresh") var y = 1\n')
        r.settings(allow=[{"rule": "settings-keys", "path": "App/*", "reason": "temporary"}])
        self.assertPasses(r.run("verify"))

    def test_bad_settings_shapes_are_errors(self):
        bad = [[], {"paths": []}, {"paths": "App/*"}, {"paths": PATHS, "live": "x"}, {"paths": PATHS, "extra": []},
               {"paths": PATHS, "retired": [{"key": "a", "migration": "m"}]},
               {"paths": PATHS, "retired": [{"key": "a", "migration": "", "reason": "r"}]},
               {"paths": PATHS, "retired": ["a"]}]
        for value in bad:
            with self.subTest(value=value):
                r = self.repo(settings_extra={"settings_keys": value})
                self.assertErrors(r.run("verify"), "settings")


class LogPrivacyTests(Base):
    CHECK = "log-privacy"

    def make(self, text):
        r = self.repo()
        r.stage("App/Log.swift", text)
        return r

    def test_public_without_a_reason_is_blocked(self):
        cases = ['logger.info("opened \\(name, privacy: .public)")',
                 'logger.info("opened \\(name, privacy:.public)")',
                 'os_log("opened %{public}@", name)',
                 'os_log("opened %{public, signpost.description:begin_time}@", name)',
                 'logger.info("x \\(n, privacy: .public)") // fine, I think',
                 'logger.info("x \\(n, privacy: .public)") // public:',
                 'logger.info("x \\(n, privacy: .public)") // public:   ']
        for line in cases:
            with self.subTest(line=line):
                r = self.make("func f() {\n    " + line + "\n}\n")
                self.assertBlocks(r.run("verify"), "FAIL log-privacy: App/Log.swift:2")
                self.assertBlocks(r.run("pre-commit"), "FAIL log-privacy")

    def test_public_with_a_reason_passes(self):
        cases = ['logger.info("opened \\(name, privacy: .public)") // public: file names are not user content',
                 'os_log("opened %{public}@", name) // public: build number',
                 'logger.info("opened \\(name, privacy: .public)") //public: x']
        for line in cases:
            with self.subTest(line=line):
                self.assertPasses(self.make("func f() {\n    " + line + "\n}\n").run("verify"))

    def test_other_privacy_levels_and_lookalikes_pass(self):
        text = ('logger.info("a \\(x, privacy: .private)")\nlogger.info("b \\(x, privacy: .sensitive)")\n'
                'os_log("c %{private}@", x)\nlet publicKey = 1 // privacy: .publicKey\nlogger.info("d \\(x)")\n')
        self.assertPasses(self.make(text).run("verify"))

    def test_the_reason_must_be_on_the_same_line(self):
        r = self.make('// public: above\nlogger.info("a \\(x, privacy: .public)")\n')
        self.assertBlocks(r.run("verify"), "App/Log.swift:2")

    def test_only_swift_files_count(self):
        r = self.repo()
        r.stage("notes.txt", "privacy: .public\n")
        self.assertPasses(r.run("verify"))

    def test_an_allowance_with_a_reason_turns_it_off(self):
        r = self.make('logger.info("a \\(x, privacy: .public)")\n')
        r.settings(allow=[{"rule": "log-privacy", "path": "App/*", "reason": "generated code"}])
        self.assertPasses(r.run("verify"))
