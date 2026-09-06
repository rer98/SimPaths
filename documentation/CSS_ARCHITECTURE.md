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
cream and beige surfaces, and the Documentation description retains its original
muted ink. Preserve the navy bands, grey navigation, white search, syntax colours
and individual funder/research accents.

`test_site_palette.py` guards these retained colours and surface treatments.
`test_site_typography.py` also protects the Documentation description's original
compact size as an intentional exception to the main prose scale. These are
source-level checks, not browser tests.

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
