"""Source contracts for restrained Documentation sidebar interactions."""

from pathlib import Path
import unittest

from test_site_typography import blocks


CSS = Path(__file__).resolve().parents[1] / "wiki/assets/css/02-shell-navigation.css"
SIDEBAR = "body.sp-docs-navigation .md-sidebar--primary"
LEAF = SIDEBAR + " .md-nav__item:not(.md-nav__item--nested) > a.md-nav__link"
SECTION = (SIDEBAR + " .md-nav--primary .md-nav__list > .md-nav__item--nested"
           " > .md-nav__container:not(.sp-nav-container-active) > a.md-nav__link")


class SidebarInteractionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = CSS.read_text()

    def test_inactive_link_hover_changes_only_ink(self):
        for link in (LEAF, SECTION):
            for state in ("hover", "focus-visible"):
                with self.subTest(link=link, state=state):
                    rules = blocks(self.source, link + f":not(.md-nav__link--active):{state}")
                    self.assertEqual(rules, [{"color": "var(--sp-sidebar-hover-ink) !important"}])

    def test_section_links_keep_their_heading_weight(self):
        for level, item, weight in ((1, "section", "580"), (2, "nested", "540")):
            selector = (SIDEBAR + f' .md-nav[data-md-level="{level}"] > .md-nav__list'
                        f" > .md-nav__item--{item} > .md-nav__container > a.md-nav__link")
            self.assertEqual(blocks(self.source, selector)[0]["font-weight"], weight)

    def test_group_hover_does_not_add_a_row_fill(self):
        for state in ("hover", "focus-within"):
            self.assertEqual(blocks(self.source, SIDEBAR + f" .md-nav__container:{state}"), [])
        rules = blocks(self.source, SIDEBAR + " .md-nav__link")
        self.assertEqual(rules[0]["transition"], "color 120ms ease")

    def test_current_page_keeps_its_marker(self):
        rule = blocks(self.source, LEAF + "--active")[0]
        self.assertEqual(rule["background"], "rgba(36, 120, 181, 0.1) !important")
        self.assertEqual(rule["border-left"], "2px solid rgba(36, 120, 181, 0.74)")
        self.assertEqual(rule["font-weight"], "600")

    def test_keyboard_focus_remains_visible_and_does_not_shift_layout(self):
        for element in ("a", "label"):
            rule = blocks(self.source, SIDEBAR + f" {element}.md-nav__link:focus-visible")[0]
            self.assertEqual(rule, {"outline": "2px solid var(--sp-sidebar-hover-ink)",
                                    "outline-offset": "-2px"})

    def test_hover_ink_has_a_dark_theme_variant(self):
        self.assertEqual(blocks(self.source, SIDEBAR)[0]["--sp-sidebar-hover-ink"], "#17658f")
        dark = '[data-md-color-scheme="slate"].sp-docs-navigation .md-sidebar--primary'
        self.assertEqual(blocks(self.source, dark)[0]["--sp-sidebar-hover-ink"], "#77c7f0")


if __name__ == "__main__":
    unittest.main()
