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

    def test_homepage_sections_share_warm_surfaces_without_changing_card_fills_or_hero(self):
        expected = {
            ".simpaths-home-intro-band": "#fffefa",
            ".simpaths-home-paths": "#f2f0e9",
            ".md-typeset .simpaths-home-paths__routes": "transparent",
            ".md-typeset .simpaths-home-paths__route": "#fffefa",
            ".simpaths-home-research-band": "#f2f0e9",
            ".md-typeset .simpaths-home-research-band a.research-entry": "#fff",
        }
        for selector, colour in expected.items():
            with self.subTest(selector=selector):
                self.assertEqual(self.rule("08-home.css", selector)["background"], colour)
        self.assertEqual(self.tokens["--sp-home-hero-bg"], "#000a2d")

    def test_use_simpaths_keeps_navy_on_box_borders_without_a_second_coloured_frame(self):
        filename = "08-home.css"
        card = self.rule(filename, ".md-typeset .simpaths-home-paths__route")
        frame = self.rule(filename, ".md-typeset .simpaths-home-paths__routes")
        self.assertEqual(card["border"], "1px solid #193449")
        self.assertEqual(frame["box-shadow"], "none")
        self.assertEqual(frame["background"], "transparent")

    def test_homepage_band_labels_follow_the_new_backgrounds(self):
        filename = "08-home.css"
        self.assertEqual(self.rule(filename, ".md-typeset .simpaths-home-paths__header h2")["color"], "#242a31")
        self.assertEqual(self.rule(filename, ".md-typeset .simpaths-home-research-band .research-header .section-heading")["color"], "#242a31")
        self.assertEqual(self.rule(filename, ".md-typeset .simpaths-home-research-band .archive-link")["color"], "#242a31 !important")

    def test_footer_keeps_navy_and_reserves_white_for_the_brand(self):
        filename = "05-site-chrome.css"
        footer = self.rule(filename, ".md-footer")
        self.assertEqual(footer["background"], "#193449")
        self.assertEqual("1px solid " + footer["background"],
                         self.rule("08-home.css", ".md-typeset .simpaths-home-paths__route")["border"])
        self.assertEqual(footer["--sp-footer-muted"], "#bbc5ce")
        for selector in (".md-footer", ".md-footer-meta", ".md-copyright",
                         ".md-copyright__highlight", ".md-social__link::after",
                         ".md-social__link:hover::after"):
            with self.subTest(selector=selector):
                self.assertEqual(self.rule(filename, selector)["color"], "var(--sp-footer-muted)")
                self.assertFalse(blocks(self.styles[filename], f'[data-md-color-scheme="slate"] {selector}'))
        self.assertEqual(self.rule(filename, ".md-copyright .footer-brand")["color"], "#fff !important")
        self.assertEqual(self.rule(filename, ".md-footer-meta")["background"], "transparent")
        self.assertNotIn(".md-footer__inner", self.styles[filename])
        self.assertNotIn("visibility: hidden", self.styles[filename].split("/* ── Previous/Next", 1)[0])
        focus = ".md-footer .md-social__link:focus-visible"
        self.assertIn(f"{focus} {{\n  outline: 2px solid #fff;", self.styles[filename])

        def luminance(hex_colour):
            channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
            return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

        contrast = (luminance(footer["--sp-footer-muted"]) + 0.05) / (luminance(footer["background"]) + 0.05)
        self.assertGreaterEqual(contrast, 4.5)

    def test_homepage_prose_uses_solid_charcoal_while_publication_metadata_stays_muted(self):
        filename = "08-home.css"
        for selector in (".md-typeset .simpaths-home-intro-band__lede",
                         ".md-typeset .simpaths-home-intro-band__body",
                         ".md-typeset .simpaths-capability-combination__features p"):
            with self.subTest(selector=selector):
                rule = self.rule(filename, selector)
                self.assertEqual(rule["color"], "#242a31")
                self.assertNotIn("opacity", rule)
        for bridge in blocks(self.styles[filename], ".md-typeset .simpaths-home-intro-band__body--bridge"):
            self.assertNotIn("color", bridge)
            self.assertNotIn("opacity", bridge)
        for selector, colour in {
            ".md-typeset .simpaths-home-research-band .research-journal": "#526171",
            ".md-typeset .simpaths-home-research-band .research-authors": "#66717d",
        }.items():
            self.assertEqual(self.rule(filename, selector)["color"], colour)

    def test_documentation_intro_and_funding_retain_original_treatments(self):
        self.assertEqual(self.rule("04-landing-components.css", ".md-typeset .docs-index__intro")["color"],
                         "rgba(31, 38, 48, 0.72)")
        funding = self.rule("06-page-sections.css", ".md-typeset .funding-page")
        self.assertEqual(funding["--funding-rule"], "#ded8cc")
        self.assertEqual(funding["--funding-copy"], "#242a31")
        self.assertEqual(funding["--funding-meta"], "#625c52")
        self.assertEqual(funding["--funding-label-bg"], "#eee9de")

    def test_documentation_sections_have_solid_surfaces_without_individual_card_frames(self):
        filename = "04-landing-components.css"
        for section, colour in {"guides": "#b9daf0", "resources": "#DF6059", "reference": "#B9318A"}.items():
            selector = f".md-typeset .docs-index__section--{section}"
            self.assertEqual(self.rule(filename, selector)["--docs-panel-background"], colour)
        self.assertEqual(self.rule(filename, ".md-typeset .docs-index__section")["background"],
                         "var(--docs-panel-background)")
        self.assertEqual(self.rule(filename, ".md-typeset .docs-index__section")["border-radius"], "6px")
        logo = (CSS_DIR.parent / "images/documentation-logo-mark.svg").read_text()
        reference = self.rule(filename, ".md-typeset .docs-index__section--reference")["--docs-panel-background"]
        for colour in ("#DB4A42", reference):
            self.assertIn(f'fill="{colour}"', logo)
        # Resources keeps the first figure's hue with a twelve-percent white lift.
        lightened = "#" + "".join(f"{round(c + (255 - c) * 0.12):02X}" for c in (219, 74, 66))
        self.assertEqual(self.rule(filename, ".md-typeset .docs-index__section--resources")["--docs-panel-background"],
                         lightened)
        link = self.rule(filename, ".md-typeset .docs-hub--index .docs-index__section a.docs-index__link")
        self.assertEqual(link["background"], "transparent")
        self.assertEqual(link["border"], "0 !important")
        self.assertNotIn("docs-card-", self.styles[filename])
        self.assertNotIn("color-mix", self.styles[filename])
        self.assertEqual(self.rule(filename, ".md-typeset .docs-index__link:focus-visible")["outline"],
                         "2px solid var(--docs-panel-ink)")

    def test_documentation_panel_text_and_focus_have_contrast(self):
        def rgb(value):
            if len(value) == 4:
                value = "#" + "".join(channel * 2 for channel in value[1:])
            return tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))

        def luminance(colour):
            linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in colour]
            return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

        def contrast(first, second):
            low, high = sorted((luminance(first), luminance(second)))
            return (high + 0.05) / (low + 0.05)

        tokens = self.rule("04-landing-components.css", ".md-typeset .docs-hub--index")
        dark = self.rule("04-landing-components.css", '[data-md-color-scheme="slate"] .md-typeset .docs-hub--index')
        for section in ("guides", "resources", "reference"):
            panel = self.rule("04-landing-components.css", f".md-typeset .docs-index__section--{section}")
            colours = {**tokens, **panel}
            surface = rgb(panel["--docs-panel-background"])
            with self.subTest(section=section):
                for role in ("--docs-panel-ink", "--docs-panel-copy"):
                    self.assertNotIn(role, dark)
                    self.assertGreaterEqual(contrast(rgb(colours[role]), surface), 4.5)
                self.assertGreaterEqual(contrast(rgb(colours["--docs-panel-ink"]), surface), 3)

    def test_reading_tables_use_the_page_surface_instead_of_a_separate_palette(self):
        for selector in (".md-typeset table:not([class])", ".md-typeset table:not([class]) th"):
            rule = self.rule("03-content.css", selector)
            self.assertEqual(rule["background"], "transparent")
        self.assertEqual(self.rule("03-content.css", ".md-typeset table:not([class])")["color"], "inherit")
        self.assertEqual(self.rule("03-content.css", ".md-typeset table:not([class]) th")["color"],
                         "inherit")

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
