# ccost

**Know exactly what Claude Code is costing you.**

Claude Code writes a full record of every API response to your machine — but nothing
tells you what it *cost*. `ccost` reads those local transcripts and computes your real
spend using Anthropic's published rates.

```
  TOTAL SPEND   $6.12   across 69 responses
  ~$6.12/day over 1 day(s)  →  $183.60/month at this rate

  TOKENS
    cache read      3.8M  ██████████████████████··  92.2%
    cache write   265.5K  ██······················   6.4%
    output         61.6K  ························   1.5%
    fresh input      138  ························   0.0%

  PROMPT CACHE
    Caching saved you $15.98 vs. running uncached
    Hit ratio 93.5%   reads 3.8M / writes 265.5K
    14.48 reads per write written — above the 2-read break-even for this cache tier
```

## Install

```sh
curl -O https://raw.githubusercontent.com/JoshDeanPro/ccost/main/ccost.py
python3 ccost.py
```

Python 3.9+. **Zero dependencies.** No API key. No network calls. Your transcripts
never leave your machine.

## Why not just add up the tokens?

Most napkin math gets this wrong in three ways, and they compound:

**1. Cache tiers are priced differently.** A 5-minute cache write costs 1.25× base
input. A 1-hour write costs **2×**. A cache read costs 0.1×. Claude Code uses all
three, and on a typical session cache traffic is *over 90% of your token volume* —
so treating it as plain input can be off by an order of magnitude.

**2. Transcripts repeat themselves.** The same API response is written to the JSONL
log multiple times. On real data that is ~70% duplicate rows. Summing every line
inflates your bill. `ccost` de-duplicates on the response id.

**3. Some things aren't tokens at all.** Web search bills $10 per 1,000 searches on
top of tokens. Fast mode reprices Opus 5 at $10/$50. `inference_geo: "us"` applies a
1.1× multiplier to everything.

`ccost` handles all of it, per model, from the published rate table.

## The cache break-even test

This is the part people find surprising.

A 1-hour cache write costs 2× base input; a read costs 0.1×. So writing once and
reading it `N` times costs `2 + 0.1N` versus `N + 1` uncached. Caching only wins once
`N ≥ 2`. For the 5-minute tier (1.25× write) it wins at `N ≥ 1`.

If your reads-per-write ratio is below that line, **prompt caching is actively costing
you money**, and no dashboard will tell you. `ccost` computes the counterfactual
directly: what you paid, versus what the identical traffic would have cost uncached.

## ccost pro — $29, one time

The free tool gives you totals, token mix, cache health, and per-model cost.

**pro** adds:

- Per-session and per-project cost attribution
- Daily spend trend
- Cache break-even analysis per session — find the sessions where caching lost money
- A ranked, dollar-quantified savings report
- Full JSON export

One-time purchase, no subscription, works offline forever.

→ **Buy: https://buy.stripe.com/3cI3cudfufOs2K2cqi48000**  ·  details: https://joshdeanpro.github.io/ccost/

```sh
ccost activate CCOST-XXXXX-XXXXX-XXXXX-XX
```

## Usage

```
ccost                 spend report for all local sessions
ccost --days 7        only the last 7 days
ccost --json          machine-readable totals
ccost activate KEY    unlock pro
```

## Accuracy

Rates are taken from Anthropic's published pricing page and verified against an
independent reimplementation (agreement to $0.000000001). If you find a discrepancy,
open an issue with `ccost --json` output — that is the whole point of this tool.

## License

MIT for the free tier. See LICENSE.
