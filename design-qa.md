# LinkParse Web Redesign QA

## 2026-08-02 admin-role refinement

- User references: the supplied `1920 x 1080` login and runtime-configuration screenshots.
- Accepted login capture: `/Users/jixu/.codex/visualizations/2026/08/01/019fbd62-c5f1-72e3-9f7d-622c04958767/linkparse-admin-refine/login-1920.png`
- Accepted runtime-policy capture: `/Users/jixu/.codex/visualizations/2026/08/01/019fbd62-c5f1-72e3-9f7d-622c04958767/linkparse-admin-refine/runtime-policy-1920.png`
- Side-by-side checks: `login-comparison.png` and `config-comparison.png` in the same folder.
- Browser viewport: `1920 x 1080` CSS pixels.

No actionable P0, P1, or P2 differences remain for the requested scope.

- Login: reduced the oversized headline and replaced the empty middle with a restrained three-step parsing flow. The form remains centered and visually unchanged enough to preserve recognition.
- Authorization: normal accounts do not render the runtime-policy navigation item. Direct `#configuration` access resolves to the parser. Administrators see the entry and the server independently enforces the role.
- Runtime policy: removed the second API Key prompt, made the signed-in administrator state explicit, and grouped controls into request safety, engine capacity, and result/CPU resources.
- Sidebar account: now presents identity and role without acting as a duplicate API Key navigation control.
- Assets: continued using the vendored Phosphor icon library; no placeholder art, custom SVG, emoji, or CSS illustration was introduced.
- Verification: registration, role promotion, admin config read, non-admin access denial, navigation visibility, and logout were exercised in the browser. Automated suite: 45 passed. Browser console final check is recorded below.

## Comparison target

- Source visual truth: `/Users/jixu/.codex/generated_images/019fbd62-c5f1-72e3-9f7d-622c04958767/exec-c5d80ee5-898a-4c5c-8f3b-9ec781fb7ff7.png`
- Source pixels: `1487 x 1058`, normalized to `1425 x 1013`
- Implementation screenshot: `/Users/jixu/.codex/visualizations/2026/08/01/019fbd62-c5f1-72e3-9f7d-622c04958767/linkparse-redesign/implementation-workspace-final.png`
- Implementation pixels: `1425 x 1013`
- Browser viewport: `1440 x 1024` CSS pixels, default device density
- State: authenticated desktop workspace, newly created account, empty recent-task state
- Full-view comparison: `/Users/jixu/.codex/visualizations/2026/08/01/019fbd62-c5f1-72e3-9f7d-622c04958767/linkparse-redesign/comparison-final.png`
- Focused comparison: `/Users/jixu/.codex/visualizations/2026/08/01/019fbd62-c5f1-72e3-9f7d-622c04958767/linkparse-redesign/comparison-final-focus.png`

## Final findings

No actionable P0, P1, or P2 differences remain.

- Fonts and typography: system UI/PingFang fallbacks reproduce the source's neutral technical hierarchy. Form labels, controls, helper text, headings, and task-table text were enlarged during the second pass to remove density drift.
- Spacing and layout rhythm: the fixed left rail, wide upload target, four-column option row, right-aligned primary action, and recent-task table follow the source composition. The final pass removed unnecessary enclosing cards so the workspace reads as one continuous surface.
- Colors and visual tokens: navy text, white base surface, pale blue active state, restrained blue CTA, green health state, and light gray dividers map to the source palette without gradients.
- Image quality and assets: the source contains no photographic or illustrative assets. Visible interface icons use the vendored Phosphor icon library; no custom SVG, CSS drawing, emoji, or placeholder artwork is used.
- Copy and content: unsupported mock fields such as language and page-range were intentionally replaced with real LinkParse controls for DPI and output formats. Mock developer/team routes were omitted because the product does not provide those capabilities.
- Accessibility and behavior: labels, focus indicators, semantic tabs, dialog names, keyboard-reachable controls, reduced-motion support, and status/error live regions are present. The 1024px Web viewport has no horizontal overflow.

The source shows populated demo task rows, while the final screenshot shows the truthful empty state for a newly registered QA account. This is expected dynamic-state variation, not a visual defect.

## Comparison history

### Pass 1

- P2: the parser, recent tasks, and result areas were wrapped in large bordered cards, retaining too much of the previous dashboard style.
- P2: form labels, inputs, helper copy, and table text were visibly smaller than the selected design.
- Fixes: flattened the primary workspace to a white base surface with dividers, removed outer card borders/radii, increased input and typography scale, and recaptured the same authenticated viewport.
- Post-fix evidence: `comparison-final.png` and `comparison-final-focus.png`.

### Pass 2

- P2: a `1024px` viewport had a 15px horizontal overflow because the body minimum width did not account for the browser scrollbar.
- Fix: reduced the desktop minimum width to `960px`; the measured `scrollWidth` and `clientWidth` are now both `1009px` at a `1024 x 768` browser viewport.

## Primary interactions tested

- Registration and automatic entry into the authenticated workspace.
- Login, session restoration, logout, and return to the authentication gate.
- Direct `/docs` access redirects to authentication without a valid session and loads normally with a valid session.
- Main navigation: parser, recent tasks, API Keys, runtime configuration, and documentation.
- Advanced parser options expand/collapse.
- API Key creation dialog opens and closes.
- Runtime concurrency fields render for OCR and OpenDataLoader.
- Browser console error log after the final pass: empty.

## Follow-up polish

- P3: populated task rows will make the recent-task region visually denser than the empty QA state; the table styles are already implemented and data-driven.

final result: passed
