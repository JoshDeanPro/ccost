#!/usr/bin/env python3
"""ccost - know exactly what Claude Code is costing you.

Reads Claude Code's local session transcripts and computes real spend using
Anthropic's published rates, including the cache tiers most calculators ignore.

No API key. No network. Nothing leaves your machine.
"""
from __future__ import annotations

import json
import os
import re
import sys
import glob
import datetime as dt
from collections import defaultdict

__version__ = "1.0.0"

# ---------------------------------------------------------------- pricing ---
# USD per million tokens: (base_input, output, write_5m, write_1h, cache_read)
# Source: platform.claude.com/docs/en/about-claude/pricing
PRICES = {
    "fable-5":   (10.0, 50.0, 12.50, 20.0, 1.00),
    "mythos-5":  (10.0, 50.0, 12.50, 20.0, 1.00),
    "opus-5":    (5.0,  25.0,  6.25, 10.0, 0.50),
    "opus-4-8":  (5.0,  25.0,  6.25, 10.0, 0.50),
    "opus-4-7":  (5.0,  25.0,  6.25, 10.0, 0.50),
    "opus-4-6":  (5.0,  25.0,  6.25, 10.0, 0.50),
    "opus-4-5":  (5.0,  25.0,  6.25, 10.0, 0.50),
    "opus-4-1":  (15.0, 75.0, 18.75, 30.0, 1.50),
    "opus-4":    (15.0, 75.0, 18.75, 30.0, 1.50),
    "sonnet-5":  (2.0,  10.0,  2.50,  4.0, 0.20),
    "sonnet-4-6":(3.0,  15.0,  3.75,  6.0, 0.30),
    "sonnet-4-5":(3.0,  15.0,  3.75,  6.0, 0.30),
    "sonnet-4":  (3.0,  15.0,  3.75,  6.0, 0.30),
    "haiku-4-5": (1.0,   5.0,  1.25,  2.0, 0.10),
    "haiku-3-5": (0.8,   4.0,  1.00,  1.6, 0.08),
}
# Fast mode (research preview) reprices Opus 5 / 4.8 at Fable-tier rates.
FAST_PRICES = (10.0, 50.0, 12.50, 20.0, 1.00)
FAST_ELIGIBLE = {"opus-5", "opus-4-8"}

WEB_SEARCH_COST = 10.0 / 1000.0   # $10 per 1,000 searches
US_GEO_MULTIPLIER = 1.1           # inference_geo="us"

_DATE_SUFFIX = re.compile(r"-\d{8}$")


def normalize_model(model: str) -> str | None:
    """Map a raw model id to a pricing key. Returns None if unknown."""
    if not model:
        return None
    m = model.strip().lower()
    m = m.split("[")[0]                    # claude-opus-5[1m] -> claude-opus-5
    m = re.sub(r"^(us\.|eu\.|apac\.)", "", m)
    m = re.sub(r"^anthropic\.", "", m)     # bedrock prefix
    m = m.split("@")[0]                    # vertex  claude-x@20250101
    m = _DATE_SUFFIX.sub("", m)
    m = re.sub(r"^claude-", "", m)
    m = re.sub(r"-v\d+:\d+$", "", m)       # bedrock version suffix
    if m in PRICES:
        return m
    for key in PRICES:                     # tolerate unseen date/variant forms
        if m.startswith(key):
            return key
    return None


class Rec:
    """One billable assistant response."""
    __slots__ = ("model", "inp", "out", "think", "read", "w5m", "w1h",
                 "searches", "cost", "ts", "project", "session", "fast", "geo_us")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k, 0))


def price_for(rec: Rec):
    if rec.fast and rec.model in FAST_ELIGIBLE:
        p = FAST_PRICES
    else:
        p = PRICES[rec.model]
    if rec.geo_us:
        p = tuple(x * US_GEO_MULTIPLIER for x in p)
    return p


def compute_cost(rec: Rec) -> float:
    bi, out, w5, w1, rd = price_for(rec)
    c = (rec.inp * bi + rec.out * out + rec.w5m * w5 +
         rec.w1h * w1 + rec.read * rd) / 1_000_000.0
    return c + rec.searches * WEB_SEARCH_COST


# ----------------------------------------------------------------- parsing ---
def transcript_files(roots=None):
    roots = roots or [os.path.expanduser("~/.claude/projects")]
    files = []
    for r in roots:
        files.extend(glob.glob(os.path.join(r, "**", "*.jsonl"), recursive=True))
    return sorted(files)


def project_name(path: str) -> str:
    parts = os.path.normpath(path).split(os.sep)
    try:
        i = parts.index("projects")
        raw = parts[i + 1]
    except (ValueError, IndexError):
        return "unknown"
    raw = raw.lstrip("-").replace("-", "/")
    return "/" + raw if raw else "unknown"


def parse(files, unknown_models=None):
    """Yield Rec objects, de-duplicated on assistant message id."""
    seen = set()
    for path in files:
        proj = project_name(path)
        session = os.path.splitext(os.path.basename(path))[0]
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                msg = o.get("message")
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue

                mid = msg.get("id")
                if mid:
                    if mid in seen:
                        continue
                    seen.add(mid)

                key = normalize_model(msg.get("model", ""))
                if key is None:
                    if unknown_models is not None and msg.get("model"):
                        unknown_models.add(msg["model"])
                    continue

                cc = usage.get("cache_creation")
                if isinstance(cc, dict):
                    w5 = cc.get("ephemeral_5m_input_tokens", 0) or 0
                    w1 = cc.get("ephemeral_1h_input_tokens", 0) or 0
                else:  # older transcripts: single undifferentiated counter
                    w5 = usage.get("cache_creation_input_tokens", 0) or 0
                    w1 = 0

                stu = usage.get("server_tool_use") or {}
                ts = o.get("timestamp") or ""

                rec = Rec(
                    model=key,
                    inp=usage.get("input_tokens", 0) or 0,
                    out=usage.get("output_tokens", 0) or 0,
                    think=((usage.get("output_tokens_details") or {})
                           .get("thinking_tokens", 0) or 0),
                    read=usage.get("cache_read_input_tokens", 0) or 0,
                    w5m=w5, w1h=w1,
                    searches=stu.get("web_search_requests", 0) or 0,
                    ts=ts, project=proj, session=session,
                    fast=(usage.get("speed") == "fast"),
                    geo_us=(usage.get("inference_geo") == "us"),
                )
                rec.cost = compute_cost(rec)
                yield rec


# ------------------------------------------------------------------- output ---
def _tty() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


class C:
    def __init__(self, on):
        self.b = "\033[1m" if on else ""
        self.d = "\033[2m" if on else ""
        self.g = "\033[32m" if on else ""
        self.y = "\033[33m" if on else ""
        self.r = "\033[31m" if on else ""
        self.c = "\033[36m" if on else ""
        self.x = "\033[0m" if on else ""


def money(v: float) -> str:
    if v >= 1000:
        return f"${v:,.0f}"
    if v >= 1:
        return f"${v:,.2f}"
    return f"${v:.4f}"


def toks(n: int) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= div:
            return f"{n/div:.1f}{unit}"
    return str(int(n))


def bar(frac: float, width: int = 24) -> str:
    frac = max(0.0, min(1.0, frac))
    n = int(round(frac * width))
    return "█" * n + "·" * (width - n)


# --------------------------------------------------------------- analysis ---
def nocache_cost(rec: Rec) -> float:
    """What this response would have cost with caching switched off."""
    bi, out, _w5, _w1, _rd = price_for(rec)
    billed_input = rec.inp + rec.read + rec.w5m + rec.w1h
    return (billed_input * bi + rec.out * out) / 1_000_000.0 + \
        rec.searches * WEB_SEARCH_COST


class Totals:
    def __init__(self):
        self.cost = 0.0
        self.nocache = 0.0
        self.inp = self.out = self.think = 0
        self.read = self.w5m = self.w1h = 0
        self.searches = 0
        self.n = 0

    def add(self, r: Rec):
        self.cost += r.cost
        self.nocache += nocache_cost(r)
        self.inp += r.inp; self.out += r.out; self.think += r.think
        self.read += r.read; self.w5m += r.w5m; self.w1h += r.w1h
        self.searches += r.searches
        self.n += 1

    @property
    def savings(self):
        return self.nocache - self.cost

    @property
    def writes(self):
        return self.w5m + self.w1h

    @property
    def hit_ratio(self):
        d = self.read + self.writes
        return (self.read / d) if d else 0.0


def collect(recs):
    grand = Totals()
    by = {k: defaultdict(Totals) for k in ("model", "project", "session", "day")}
    for r in recs:
        grand.add(r)
        by["model"][r.model].add(r)
        by["project"][r.project].add(r)
        by["session"][(r.project, r.session)].add(r)
        by["day"][(r.ts or "")[:10] or "unknown"].add(r)
    return grand, by


def _table(rows, cols, c: C, limit=None):
    if limit:
        rows = rows[:limit]
    if not rows:
        return
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows))
              for i, (h, _) in enumerate(cols)]
    head = "  ".join(f"{h:<{widths[i]}}" if cols[i][1] == "l"
                     else f"{h:>{widths[i]}}" for i, (h, _) in enumerate(cols))
    print(f"  {c.d}{head}{c.x}")
    for r in rows:
        line = "  ".join(f"{str(v):<{widths[i]}}" if cols[i][1] == "l"
                         else f"{str(v):>{widths[i]}}" for i, v in enumerate(r))
        print(f"  {line}")


def report(grand: Totals, by, c: C, pro: bool, days_span: int):
    print()
    print(f"{c.b}  ccost {__version__}{c.x}  {c.d}Claude Code spend, computed from local transcripts{c.x}")
    print(f"  {c.d}{'─'*66}{c.x}")

    print(f"\n  {c.b}TOTAL SPEND{c.x}   {c.b}{c.g}{money(grand.cost)}{c.x}"
          f"   {c.d}across {grand.n:,} responses{c.x}")
    if days_span > 0:
        per_day = grand.cost / days_span
        print(f"  {c.d}~{money(per_day)}/day over {days_span} day(s)"
              f"  →  {money(per_day*30)}/month at this rate{c.x}")

    # ---- token mix
    print(f"\n  {c.b}TOKENS{c.x}")
    mix = [("cache read", grand.read), ("cache write", grand.writes),
           ("output", grand.out), ("fresh input", grand.inp)]
    tot_tok = sum(v for _, v in mix) or 1
    for label, v in mix:
        print(f"    {label:<12} {toks(v):>7}  {c.c}{bar(v/tot_tok)}{c.x} {v/tot_tok*100:5.1f}%")
    if grand.think:
        print(f"    {c.d}(thinking tokens included in output: {toks(grand.think)}){c.x}")

    # ---- the headline insight
    print(f"\n  {c.b}PROMPT CACHE{c.x}")
    if grand.writes or grand.read:
        s = grand.savings
        if s >= 0:
            print(f"    Caching {c.g}saved you {money(s)}{c.x} vs. running uncached")
        else:
            print(f"    {c.r}Caching COST you {money(-s)} extra{c.x} vs. running uncached")
        print(f"    Hit ratio {grand.hit_ratio*100:.1f}%   "
              f"{c.d}reads {toks(grand.read)} / writes {toks(grand.writes)}{c.x}")
        if grand.writes:
            rpw = grand.read / grand.writes
            need = 2.0 if grand.w1h > grand.w5m else 1.0
            verdict = (f"{c.g}above{c.x}" if rpw >= need else f"{c.r}BELOW{c.x}")
            print(f"    {rpw:.2f} reads per write written — {verdict} the "
                  f"{need:.0f}-read break-even for this cache tier")
    else:
        print(f"    {c.d}no cache activity recorded{c.x}")

    if grand.searches:
        print(f"\n  {c.b}SERVER TOOLS{c.x}\n    web search  {grand.searches:,} × $0.010 = "
              f"{money(grand.searches*WEB_SEARCH_COST)}  {c.d}(web fetch is free){c.x}")

    # ---- by model
    print(f"\n  {c.b}BY MODEL{c.x}")
    rows = sorted(by["model"].items(), key=lambda kv: -kv[1].cost)
    _table([(k, money(t.cost), f"{t.cost/grand.cost*100:.1f}%" if grand.cost else "-",
             f"{t.n:,}") for k, t in rows],
           [("model", "l"), ("cost", "r"), ("share", "r"), ("calls", "r")], c)

    # ---- by project
    print(f"\n  {c.b}BY PROJECT{c.x}")
    rows = sorted(by["project"].items(), key=lambda kv: -kv[1].cost)
    shown = rows if pro else rows[:3]
    _table([(k[-46:], money(t.cost), f"{t.n:,}") for k, t in shown],
           [("project", "l"), ("cost", "r"), ("calls", "r")], c)
    if not pro and len(rows) > 3:
        print(f"  {c.d}… {len(rows)-3} more project(s) — ccost pro{c.x}")

    if not pro:
        print(f"\n  {c.d}{'─'*66}{c.x}")
        print(f"  {c.y}ccost pro{c.x} adds per-session costs, the daily trend, "
              f"cache\n  break-even analysis and a ranked savings report.")
        print(f"  {c.d}Unlock: {c.x}{c.c}https://joshdeanpro.github.io/ccost/{c.x}"
              f"{c.d}   then: ccost activate <key>{c.x}")
        print()
        return

    # -------------------------------------------------- PRO
    print(f"\n  {c.b}DAILY TREND{c.x}")
    rows = sorted(by["day"].items())
    peak = max((t.cost for _, t in rows), default=0) or 1
    for day, t in rows[-14:]:
        print(f"    {day}  {money(t.cost):>10}  {c.c}{bar(t.cost/peak, 30)}{c.x}")

    print(f"\n  {c.b}TOP SESSIONS BY COST{c.x}")
    rows = sorted(by["session"].items(), key=lambda kv: -kv[1].cost)
    _table([(p[-28:], s[:8], money(t.cost), f"{t.n:,}",
             f"{t.hit_ratio*100:.0f}%") for (p, s), t in rows],
           [("project", "l"), ("session", "l"), ("cost", "r"),
            ("calls", "r"), ("cache hit", "r")], c, limit=12)

    # ---- ranked savings opportunities
    print(f"\n  {c.b}SAVINGS OPPORTUNITIES{c.x}  {c.d}(ranked by dollars){c.x}")
    findings = []

    for (p, s), t in by["session"].items():
        if t.savings < -0.01:
            findings.append((-t.savings,
                             f"Session {s[:8]} in {p.split('/')[-1] or p}: caching cost "
                             f"{money(-t.savings)} MORE than it saved "
                             f"({t.read/t.writes if t.writes else 0:.1f} reads/write). "
                             f"Shorter-lived context or a 5m breakpoint would be cheaper."))

    if grand.w1h:
        rpw = grand.read / grand.w1h if grand.w1h else 0
        if rpw < 2:
            bi = PRICES[max(by['model'], key=lambda m: by['model'][m].cost)][0]
            waste = grand.w1h * bi * (2.0 - 1.25) / 1_000_000.0
            findings.append((waste,
                             f"1-hour cache writes are only read {rpw:.1f}× on average "
                             f"(break-even is 2×). Using the 5-minute tier instead would "
                             f"have cost about {money(waste)} less."))

    opus = sum(t.cost for m, t in by["model"].items() if m.startswith(("opus", "fable", "mythos")))
    if grand.cost and opus / grand.cost > 0.9 and grand.cost > 1:
        est = opus * 0.5
        findings.append((est,
                         f"{opus/grand.cost*100:.0f}% of spend is on Opus-tier models. "
                         f"Routing mechanical work (formatting, test scaffolding, renames) "
                         f"to Sonnet 5 would cut roughly {money(est)} at current volume."))

    if grand.searches * WEB_SEARCH_COST > 0.5:
        findings.append((grand.searches*WEB_SEARCH_COST,
                         f"{grand.searches:,} billed web searches "
                         f"({money(grand.searches*WEB_SEARCH_COST)}). Web *fetch* is free — "
                         f"prefer it when you already know the URL."))

    if findings:
        for amt, text in sorted(findings, reverse=True)[:6]:
            print(f"    {c.y}▸{c.x} {c.b}{money(amt):>9}{c.x}  {text}")
    else:
        print(f"    {c.g}✓{c.x} No material waste detected. Cache ratios and model "
              f"mix look healthy.")
    print()


# -------------------------------------------------------------- licensing ---
LICENSE_PATH = os.path.expanduser("~/.ccost/license.json")


def _check(key: str) -> bool:
    """Offline structural check: CCOST-XXXXX-XXXXX-XXXXX-CC (mod-37 checksum)."""
    k = key.strip().upper()
    if not re.fullmatch(r"CCOST(-[0-9A-Z]{5}){3}-[0-9A-Z]{2}", k):
        return False
    body = k.replace("-", "")
    payload, check = body[:-2], body[-2:]
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    total = sum(alphabet.index(ch) * (i + 1) for i, ch in enumerate(payload))
    exp = alphabet[total % 36] + alphabet[(total // 36) % 36]
    return exp == check


def load_license():
    try:
        with open(LICENSE_PATH) as f:
            k = json.load(f).get("key", "")
        return k if _check(k) else None
    except Exception:
        return None


def activate(key: str) -> int:
    if not _check(key):
        print("Invalid license key. Keys look like CCOST-A1B2C-D3E4F-G5H6J-KM")
        return 1
    os.makedirs(os.path.dirname(LICENSE_PATH), exist_ok=True)
    with open(LICENSE_PATH, "w") as f:
        json.dump({"key": key.strip().upper(), "activated": dt.date.today().isoformat()}, f)
    print("ccost pro activated. Run `ccost` for the full report.")
    return 0


# -------------------------------------------------------------------- main ---
USAGE = """ccost - what Claude Code actually costs you

  ccost                 spend report for all local sessions
  ccost --json          machine-readable totals
  ccost --days N        only the last N days
  ccost activate KEY    unlock ccost pro
  ccost --version

No API key. No network calls. Your transcripts never leave the machine.
"""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "activate":
        return activate(argv[1]) if len(argv) > 1 else (print(USAGE) or 1)
    if "-h" in argv or "--help" in argv:
        print(USAGE); return 0
    if "--version" in argv:
        print(f"ccost {__version__}"); return 0

    days = None
    if "--days" in argv:
        try:
            days = int(argv[argv.index("--days") + 1])
        except (IndexError, ValueError):
            print("--days needs a number"); return 1

    files = transcript_files()
    if not files:
        print("No Claude Code transcripts found in ~/.claude/projects.")
        return 1

    unknown = set()
    recs = list(parse(files, unknown))

    if days:
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
        recs = [r for r in recs if (r.ts or "") >= cutoff]

    if not recs:
        print("No billable responses found in that window.")
        return 1

    grand, by = collect(recs)
    stamps = sorted(r.ts[:10] for r in recs if r.ts)
    span = 1
    if stamps:
        try:
            a = dt.date.fromisoformat(stamps[0]); b = dt.date.fromisoformat(stamps[-1])
            span = (b - a).days + 1
        except ValueError:
            span = 1

    if "--json" in argv:
        print(json.dumps({
            "version": __version__,
            "total_cost_usd": round(grand.cost, 6),
            "uncached_equivalent_usd": round(grand.nocache, 6),
            "cache_savings_usd": round(grand.savings, 6),
            "cache_hit_ratio": round(grand.hit_ratio, 4),
            "responses": grand.n,
            "days_span": span,
            "tokens": {"input": grand.inp, "output": grand.out,
                       "thinking": grand.think, "cache_read": grand.read,
                       "cache_write_5m": grand.w5m, "cache_write_1h": grand.w1h},
            "web_searches": grand.searches,
            "by_model": {m: round(t.cost, 6) for m, t in by["model"].items()},
        }, indent=2))
        return 0

    report(grand, by, C(_tty()), pro=bool(load_license()), days_span=span)
    if unknown:
        print(f"  note: {len(unknown)} unrecognized model id(s) skipped: "
              f"{', '.join(sorted(unknown)[:3])}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
