"""Source-level contracts for the retained palette; these are not browser tests."""

from pathlib import Path
import unittest

from test_site_typography import blocks


CSS_DIR = Path(__file__).resolve().parents[1] / "wiki" / "assets" / "css"


class SitePaletteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.styles = {path.name: path.read_text() for path in CSS_DIR.glob("*.css")}
        cls.tokens = {key: value for rule in blocks(cls.styles["01-foundation.css"], ":root")
                      for key, value in rule.items()}

    def rule(self, filename, selector):
        found = blocks(self.styles[filename], selector)
        self.assertTrue(found, f"Missing selector: {selector}")
        return {key: value for rule in found for key, value in rule.items()}

    def test_original_light_palette_is_preserved(self):
        expected = {
            "--sp-paper": "#FAF9F5", "--sp-midnight": "#2A3848",
            "--sp-accent": "#2478b5", "--sp-ghost": "#C3C6D5",
            "--sp-surface": "rgba(255,255,255,0.74)",
            "--sp-surface-strong": "rgba(255,255,255,0.9)",
        }
        for token, value in expected.items():
            self.assertEqual(self.tokens[token], value)
        for token in ("--sp-ink", "--sp-white", "--sp-stone", "--sp-tray", "--sp-muted"):
            self.assertNotIn(token, self.tokens)
        light = self.rule("01-foundation.css", '[data-md-color-scheme="default"]')
        self.assertEqual(light["--md-default-bg-color"], "var(--sp-paper)")
        self.assertEqual(light["--md-default-bg-color--light"], "#f4f3ef")
        self.assertNotIn("--md-default-fg-color", light)

    def test_homepage_bands_keep_their_original_surfaces(self):
        expected = {
            ".simpaths-home-intro-band": "#fffefa",
            ".simpaths-home-paths": "#f2f0e9",
            ".md-typeset .simpaths-home-paths__routes": "#dedad0",
            ".md-typeset .simpaths-home-paths__route": "#fffefa",
            ".simpaths-home-research-band": "#193449",
            ".simpaths-home-citation-band": "#e7dfd2",
        }
        for selector, colour in expected.items():
            with self.subTest(selector=selector):
                self.assertEqual(self.rule("08-home.css", selector)["background"], colour)
        self.assertEqual(self.tokens["--sp-home-hero-bg"], "#000a2d")

    def test_documentation_and_funding_retain_original_treatments(self):
        self.assertEqual(self.rule("04-landing-components.css", ".md-typeset .docs-index__intro")["color"],
                         "rgba(31, 38, 48, 0.72)")
        self.assertEqual(self.rule("04-landing-components.css", ".md-typeset a.docs-index__card")["background"],
                         "#fff")
        funding = self.rule("06-page-sections.css", ".md-typeset .funding-page")
        self.assertEqual(funding["--funding-rule"], "#ded8cc")
        self.assertEqual(funding["--funding-copy"], "#242a31")
        self.assertEqual(funding["--funding-meta"], "#625c52")
        self.assertEqual(funding["--funding-label-bg"], "#eee9de")
        self.assertEqual(self.rule("03-content.css", ".md-typeset table:not([class])")["background"],
                         "#fffefa")
        self.assertEqual(self.rule("03-content.css", ".md-typeset table:not([class]) th")["background"],
                         "#ece8df")

    def test_funder_and_research_accents_are_preserved(self):
        expected = {
            "nihr": "#0051c2", "horizon-europe": "#003399", "phi": "#f0d764",
            "chanse-norface": "#42bccd", "inapp": "#18376e", "health-foundation": "#de0031",
            "jpi": "#3c76bb", "erc": "#ff7d00", "espon": "#63b9ea",
        }
        for funder, colour in expected.items():
            selector = f'.md-typeset .funding-entry[data-funder="{funder}"]'
            self.assertEqual(self.rule("06-page-sections.css", selector)["--funding-brand"], colour)
        for colour in ("#c62e67", "#7040a3", "#1f70aa"):
            self.assertIn(f"--research-accent: {colour};", self.styles["08-home.css"])

    def test_grey_navigation_white_search_and_syntax_colours_are_unchanged(self):
        self.assertEqual(self.rule("02-shell-navigation.css", ".md-tabs")["background"],
                         "rgba(8,16,32,0.45) !important")
        self.assertIn("--sp-search-surface: #fff;", self.styles["05-site-chrome.css"])
        self.assertIn("--sp-code-bg: #ffffff;", self.styles["03-content.css"])

    def test_dark_palette_is_preserved(self):
        dark = self.rule("01-foundation.css", '[data-md-color-scheme="slate"]')
        self.assertEqual(dark["--md-default-bg-color"], "#141e2a")
        self.assertEqual(dark["--md-default-fg-color"], "rgba(250,249,245,0.92)")
        self.assertEqual(dark["--sp-surface"], "rgba(25,37,49,0.84)")


if __name__ == "__main__":
    unittest.main()
