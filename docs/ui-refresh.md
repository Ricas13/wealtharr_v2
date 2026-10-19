# Wealtharr owner UI refresh

The new visual system lives in `static/css/refresh.css`, with self-hosted icons at `static/icons/ui.svg` and progressive enhancement in `static/js/refresh.js`. It uses no frontend framework, Node build step or external font dependency.

## Design rationale

A personal-finance screen should prioritise real portfolio value, net capital added and actionable notices before setup promotions. The Overview separates real money from purple-labelled optional paper simulation. Strategy pages use server-rendered native query-string tabs: `overview`, `signals`, `ledger` and `setup`.

## Financial integrity

The result is value minus net deposits, not time-weighted return. A simple gain ratio appears only for positive contributions and no withdrawals. Charts and tiny strategy sparklines use actual recorded valuations, preserving real date spacing; they never substitute guessed closing prices or backtest returns. The displayed equity weight is compared with the initial/reset 60% reference, not presented as a standing rebalance target. All trades remain owner-entered.

## UI design tokens

Dark background `#080f1c`, panel `#111e31`, primary action `#5bc6fc`, positive `#69e2b7`, negative `#ff9ca8`, warning `#ffd28d`, paper `#bda2f8`. Spacing steps 4/8/12/16/24/32/48px. Light-theme variants are defined in the CSS. Buttons have 44px targets (40px for compact), financial figures use tabular numerals, and reusable panels and pills are token-driven.

## Accessibility and rollout

The shell has a skip link, keyboard navigation and responsive tab links. All charts have HTML data tables, range buttons announce pressed state, chart observations support keyboard focus, and reduced-motion preferences are honoured. Test both themes at 360px and 200% zoom as well as desktop; legacy pages retain some previous hard-coded colours.

Django and WhiteNoise remain unchanged. Production deploy stays manual on `oraclefree2`; preserve `.env.production`, `compose.override.yml`, `media_net` and the persistent PostgreSQL volume. Validate the 9Sig manual ledger and signal confirmation flows after deploying.
