# Initial watchlist: AI Conviction

The user selected these 20 symbols from a screenshot, in this order:

MU, AVGO, SMCI, TSM, MRVL, NVDA, AMD, OKLO, SMR, LEU, SNOW, CRWV, ANET, ORCL, VRT, CCJ, GEV, BE, UEC, SKHY.

The machine-readable seed is [ai-conviction.json](../config/watchlists/ai-conviction.json). Symbols are transcribed as shown, including SKHY; none has been silently substituted. Screenshot quotes, changes and percentages are not market observations and were not imported.

This is configuration data for the first watchlist, not an active runtime watchlist. A narrow runtime importer now exists for the web UI starter data (`seed-starter-data`, see docs/UI.md): it registers these 20 symbols as operator-ATTESTED US stocks from a packaged copy, `src/portfolio_agent/starter_data.json`, which a test keeps identical to this file's symbols and order. SKHY is recorded as the user's attestation only. That importer is not authoritative validation, does not change the `pending` statuses below, and does not make the watchlist holdings or monitoring membership. Every entry still awaits authoritative instrument resolution and eligibility validation. Do not infer US eligibility from a ticker alone: domicile, exchange listing, ADR structure and instrument type require the documented eligibility policy. Preserve unresolved symbols for reconciliation rather than guessing an identifier.

Before runtime import, assign the watchlist to an authenticated owner. A watchlist does not imply a holding, research-universe membership or an enhanced-monitoring slot. Admission to those sets remains explicit. The 400 research target and 75 enhanced-monitoring cap are unchanged. This user-selected seed must not be treated as synthetic financial test data.
