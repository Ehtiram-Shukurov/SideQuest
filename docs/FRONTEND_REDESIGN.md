# SideQuest frontend redesign

## What changed

The served application in `web/` now uses a quiet city-guide identity: cool neutral surfaces, deep teal actions, locally hosted Manrope type, and an original S-shaped route mark. The typography, buttons, inputs, cards, status messages, timeline, map markers, group room, sharing, and replan review share one CSS system. Both light and dark palettes are explicit.

The animated contour background, orange compass, rotating headline, pointer spotlights, ripples, and perpetual route animations were removed. Reduced-motion preferences still disable the remaining loading animation. The standalone root `index.html` is a historical prototype; FastAPI serves `web/index.html`.

The composer now has a shorter introduction, Solo/With friends selection, quiet example chips, an expanding request field, grouped constraints, collapsible weather preferences, and secondary data settings. The header reflects Live versus Demo immediately. A clearly labeled recorded example is available without a model key.

Location is requested only after an explicit click. Users can alternatively enter latitude and longitude from a map pin. Empty, nonnumeric, and out-of-range coordinates are rejected; zero is valid. A late GPS response cannot overwrite a newer manual selection. Failed GPS requests preserve a previously selected starting point. Manual entries continue to use the device timezone, which the interface states; this is not a new address-search or destination-timezone feature.

The existing planning, group, export, history, sharing, and replan API contracts are unchanged. Unknown facts, estimated prices, replay notices, and source limitations remain visible. Technical token/tool counts now live under Planning activity.

## Assets

- `web/assets/sidequest-icon.svg`: app icon and favicon.
- `web/assets/sidequest-mark.svg`: monochrome editable vector.
- `web/assets/fonts/manrope-variable.ttf`: locally served Manrope variable font.
- `web/assets/fonts/OFL.txt`: bundled font license.

No framework migration, production Node dependency, external font request, backend dependency change, or CSP relaxation is required.

## Validation

- `uv run pytest -q`: 105 tests pass.
- `npm ci && npm test`: 12 DOM interaction regressions pass (Node 18+).
- `node --check web/app.js` and `node --check web/fx.js`: pass.
- `git diff --check`: pass.
- Palette checks: primary, secondary, warning, and error text exceed WCAG AA normal-text contrast. Control boundaries exceed 3:1 against white.

The optional Node package exists only for tests. The usual Python/Render startup continues to serve the frontend directly.

This execution environment needed the optional `socksio` package locally for its HTTP proxy. It is not added to the application dependencies.

## Remaining visual review

The cloud browser rejected `http://localhost:8000` with `net::ERR_BLOCKED_BY_CLIENT`. Consequently no local screenshot comparison, rendered viewport/overflow assertion, or live browser geolocation/Leaflet check is claimed. The DOM tests verify behavior, not rendered layout.

Before deployment, open the local app and review the following:

1. At widths 320, 390, 768, 1024, and 1440, check landing form, labels, budget row, location states, and the primary action. Include a short-height viewport and 200% zoom.
2. Switch light/dark themes; use keyboard focus and mobile section-tab arrow keys.
3. Open `/?replay=1`; check the itinerary, warnings, map loading/fallback, and mobile Plan/Map/Adjust views.
4. Create a test group, add availability, and inspect organizer/member views, invitation controls, shared plans, and replan diffs.
5. Exercise actual location permission granted/denied and manual coordinates on the target browser.

Changes are prepared locally; nothing has been pushed to GitHub or deployed to Render.
