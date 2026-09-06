# Documentation CSS architecture

The documentation theme is divided into ordered layers under
`documentation/wiki/assets/css/`. MkDocs loads them in the order listed below.
Changing that order can alter the cascade even when no selector changes.

1. `01-foundation.css`: fonts, tokens, palette, dark mode, and global links.
2. `02-shell-navigation.css`: header, tabs, sidebars, and documentation filtering.
3. `03-content.css`: typography, equations, code, admonitions, and tables.
4. `04-landing-components.css`: the documentation landing page only.
5. `05-site-chrome.css`: footer, search, buttons, and shared responsive chrome.
6. `06-page-sections.css`: research, funding, modules, and other page-specific
   sections.
7. `07-roadmap.css`: the development roadmap and its compact contents view.
8. `08-home.css`: the homepage system and its responsive refinements.

`assets/js/site-state.js` is the single adapter between Material's generated
markup and the styling layer. It exposes explicit `sp-page-*`, `sp-tab-*`,
`sp-search-open`, and navigation state classes. CSS should consume those
classes rather than rediscovering page state with relational selectors.

## Shared typography convention

The approved reading hierarchy is site-wide, not a page-specific experiment.
Define its tokens once in `01-foundation.css` and consume them in `03-content.css`
and the relevant component layer:

- Page titles: `--sp-title-size` / `--sp-title-weight` (1.8rem / 560).
- Section headings: `--sp-section-size` / `--sp-section-weight` (1.16rem / 520).
- Subheadings: `--sp-subheading-size` / `--sp-subheading-weight` (0.95rem / 560).
- Main prose: `--sp-reading-copy-size` / `--sp-reading-line-height` (0.82rem / 1.75).
- Supporting labels: `--sp-label-size` / `--sp-label-weight` (0.7rem / 550).
- Publication, grant and status metadata: `--sp-meta-size` (0.62rem).
- Sidebar links and the contents rail: `--sp-nav-size` / `--sp-toc-size`.

Use these roles across Model, Documentation, Validation, Research, Funding,
Roadmap and the homepage. Do not reintroduce a separate heavy heading scale or
shrink main prose at mobile breakpoints. The homepage wordmarks and large band
headings, the Documentation masthead's mobile title and its compact description
(0.72rem / 1.6), featured publication titles, compact ledger titles, equations,
code and small supporting notes retain their
purpose-specific treatments. Sharing typography does not mean sharing layouts.

Long reading pages use `--sp-reading-measure` on the content container. Keep the
homepage bands and catalogue/list layouts at their existing widths; do not add
per-sentence width constraints or balanced/pretty wrapping. Palette changes are
separate from this convention and must not change typography or layout.

Run `python3 -m unittest discover -s documentation/scripts -p 'test_*.py'` to check
the shared scale and component adoption as well as code highlighting.

## Palette preservation

Keep the existing warm palette independent of typography refinements. The
neutral-grey and white/charcoal colour trials were not approved for publication.
Reading pages retain `--sp-paper` (#FAF9F5), the homepage retains its existing
cream introduction and card surfaces, and the Documentation description retains its original
muted ink. Preserve the navy bands, grey navigation, white search, syntax colours
and individual funder/research accents.

Homepage introductory paragraphs and feature descriptions use solid charcoal
(#242a31), without separate faded lead/body colours. This is a text-only
exception: retain the existing layout, heading scale and muted publication metadata.
The introductory paragraphs share the 520 weight of the "The framework..."
bridge sentence; feature descriptions keep their existing weight. Do not
restore a lighter lead/body weight or increase their font size to compensate.

The homepage's "Use SimPaths" section retains its navy #193449 band and white
heading, with light #fffefa boxes in the original beige #dedad0 surround.
"Selected Research" stays warm #f2f0e9 with charcoal headings, white cards and
its existing accents. Keep the sections distinct: do not match their backgrounds
or turn research near-white. Preserve spacing, destinations and the research-card
treatments. Remove Material's trailing article margin on the homepage only so
the final research band meets the footer.
The shared footer retains navy #193449 in both themes; only the SimPaths brand is
white, while the sentence-case description and links use readable grey #bbc5ce.
It contains site identity and links, not page navigation. Disable the theme's
generator line through `extra.generator: false` rather than hiding a line that
still occupies space.

Previous/Next lives after the article content through `partials/page-navigation.html`,
using MkDocs' actual previous/next pages and respecting `hide: [footer]`.
Keep it absent on the homepage. The two compact outlined links share the article's
width, use sentence-case direction labels and wrapping page titles, and stack
on small screens. Retain native links, keyboard focus and reduced-motion support.
Do not move navigation between containers with JavaScript or restore the tall
full-width pager inside the navy footer.

The Documentation directory uses solid section panels: light-blue Guides with
a two-column link grid, then coral-orange Resources and magenta-purple Reference
side by side. Resources uses #DF6059, a twelve-percent white lift of #DB4A42 from
the first logo figure; Reference uses #B9318A from the third figure from the red/left
end. Panels use a small 6px
corner radius. The approved Resources fallback is light green #c8e3bd, with
ink #274334, copy #3f5643 and divider rgba(39, 67, 52, 0.25).
Guides and Resources define dark text, divider and focus colours; Reference
retains light text. These colour pairs apply in both site themes.
Links sit directly on each section surface with fine dividing rules and visible
keyboard focus, not in individual coloured cards. Keep link surfaces unchanged on
hover, focus and press; only arrows move, without shifting text. Do not restore
highlight fills or animated underlines. Disable motion for reduced-motion preferences. The compact masthead
has an inline mark in its original padded, pure-white box on the left and a
full-width description. The white box retains the light logo variant in both
themes and reserves space before rendering. Keep the approved typography
and content measure; stack the panels and links on small screens. Do not restore
the pastel card fills, coloured edge stripes or separate card frames.

`test_site_palette.py` guards these retained colours and surface treatments.
`test_site_typography.py` also protects the Documentation description's original
compact size as an intentional exception to the main prose scale. These are
source-level checks, not browser tests.

Keep the footer description in sentence case with normal letter spacing:
"An open-source microsimulation initiative." The SimPaths brand stays unchanged.

## Headings and reading tables

Do not wrap heading labels in Markdown bold. Nested `strong`/`b` elements in
headings and table headers inherit their container's weight; emphasis in body
copy remains unchanged.

Reading tables keep body-sized values, compact rows and open edges. Column
labels use the shared label size in monospace with natural capitalisation and
normal letter spacing, separated from the values by a 2px neutral rule. Both
labels and the rule inherit the surrounding text colour in light and dark mode.
Light row separators, modest first-column emphasis
and tabular figures support scanning. Use existing ink/border tokens, transparent
surfaces and the same convention in setup guides; do not restore shaded header
panels or an outer frame. Code and math in
headers retain their original case. Keep Material's scrolling wrapper for wide
tables, preserve author-specified column alignment, and exclude code-layout
tables such as `.highlighttable` from these styles.

Directory trees are reference diagrams, not commands: mark the repository tree
fence with `{.text .no-copy}`. Material's native opt-out removes only its copy
button; command and code snippets remain copyable.

## Rules for future changes

- Put a rule in the narrowest appropriate component file.
- Preserve the order in `mkdocs.yml` unless a cascade change is intentional.
- Prefer a component class over another global Material-theme override.
- Avoid new `!important` declarations. The guard rejects all `:has()`
  selectors; add a narrowly named state in `site-state.js` instead.
- Do not add page-level `<style>` blocks. Move reusable styling into the
  appropriate component file.
- Remove a component's CSS when its final markup is removed. The retired
  generic hero and card systems are guarded against accidental restoration.
- Run the architecture check and strict MkDocs build before publishing.

```bash
bash documentation/scripts/check-docs-css.sh
mkdocs build --strict
cd documentation/visual-tests && npm test
```

The budgets are set against the refactored baseline, with limited headroom for
deliberate additions. If a file approaches its cap, simplify or extract a
coherent responsibility instead of raising the limit by default.

The browser tests cover representative desktop and mobile routes, assert basic
layout invariants, and save full-page screenshots under `test-results/`. The
deployment workflow uploads those screenshots as an artifact so visual changes
can be reviewed without making pixel-level rendering differences block a
release.
