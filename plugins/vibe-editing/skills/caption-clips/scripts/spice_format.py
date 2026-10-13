#!/usr/bin/env python3
"""
spice_format.py — the reference editor's company.com caption-text formatter.

Pure-Python (stdlib only), deterministic text normalizer that implements the
9-rule spec at:
    caption-clips/references/spice_caption_formatting_spec.md

Public API:
    format_caption_text(text: str) -> str

CLI:
    python3 spice_format.py        # reads stdin, writes formatted stdout

Processing order (per spec "Processing Order"):
    1. Common words dictionary (FIRST + authoritative — locks tokens)
    2. Number words -> digits (incl. compound numbers)
    3. "one" vs "1"
    4. Listicle & sequence labels  ("number N" -> "#N"; "step one" -> "step #1" / "day 1")
    5. Money formatting
    6. Symbol & multiplier rule  (dollars/percent -> $/% ; times/x -> X)
    7. Thousands separators
    8. Capitalization
    9. Punctuation cleanup

Idempotency: tokens already in correct output form ($1K, ROI, 1-on-1, AI, 10X, 1,000)
are not re-processed.

Implementation strategy
------------------------
The text is tokenized into a list of Token objects. Each token carries its surface
text plus a `locked` flag. Once the dictionary (or a later rule that produces a final
form, e.g. money) locks a token, subsequent rules skip it. This makes the dictionary
authoritative and gives us idempotency for free (already-correct inputs match the
dictionary / lock patterns and are left alone).
"""

import re
import sys
from decimal import Decimal


# ---------------------------------------------------------------------------
# Token model
# ---------------------------------------------------------------------------

class Token:
    """A single token: word text + leading/trailing punctuation captured separately."""

    __slots__ = ("text", "lead", "trail", "locked", "space_before", "spoken", "src")

    def __init__(self, text, lead="", trail="", space_before=True, spoken=False, src=None):
        self.text = text          # the "word" core (may itself contain $ , . etc once formatted)
        self.lead = lead          # leading punctuation attached in source
        self.trail = trail        # trailing punctuation attached in source
        self.locked = False       # if True, no further rule touches .text
        self.space_before = space_before
        # True if this digit token was produced by number-WORD expansion (e.g.
        # "two thousand"->2000). Rule 8 thousands separators + the year guard apply
        # to SPOKEN values only; a pre-existing digit literal ("5000", "2024") is left
        # untouched (provenance per Rule 8: "Spoken-number values ONLY").
        self.spoken = spoken
        # Source-word index provenance (additive metadata — NEVER affects text output).
        # Set by format_words to the list of input-word indices this token was built from;
        # merged forward whenever a rule creates a new token from existing tokens, so the
        # final token can recover the start/end timestamps of its constituent words.
        self.src = list(src) if src else []

    def __repr__(self):
        return f"Token({self.lead!r}+{self.text!r}+{self.trail!r}, locked={self.locked}, spoken={self.spoken})"


# A token's core may legitimately contain these chars after processing.
_WORD_RE = re.compile(r"\S+")


def _merge_src(*toks):
    """Concatenate the .src lists of every source token consumed to build a new one.
    Additive provenance only — has zero effect on text output."""
    out = []
    [out.extend(getattr(t, "src", []) or []) for t in toks]
    return out


def _peel_token(raw):
    """Peel one raw chunk into (core, lead, trail) using the same logic tokenize uses.

    Abbreviations / initialisms / domains keep their internal+trailing periods; other
    trailing sentence punctuation (, . ? !) and leading quotes/parens are peeled off so
    rule logic sees a clean core. Shared by tokenize() (text mode) and format_words()
    (per-word mode) so both peel identically.
    """
    lead = ""
    trail = ""
    core = raw

    # Abbreviations / initialisms / domains keep their internal+trailing periods.
    # Detect them up front so we don't peel a meaningful trailing period.
    low = core.lower().rstrip(",?!")
    is_protected = (
        low in ("etc.", "e.g.", "i.e.", "u.s.")
        or re.fullmatch(r"(?:[a-z]\.){2,}", low)          # initialism like u.s. / a.b.c.
        or re.search(r"[a-z0-9]\.[a-z]{2,}$", low)         # domain like company.com
    )

    if is_protected:
        # Peel trailing ? ! and COMMAS that come AFTER the abbreviation/domain's
        # final period — these are sentence punctuation (dropped in Rule 2) and must
        # NOT break the dictionary key match. The meaningful closing period of
        # "etc." / "e.g." / "U.S." stays in the core; "e.g.," -> core "e.g." + ",".
        mt = re.search(r"[,\?\!]+$", core)
        if mt:
            trail = mt.group(0)
            core = core[: mt.start()]
    else:
        # Peel trailing sentence punctuation we manage: , . ? ! (and combos).
        mt = re.search(r"[\,\.\?\!]+$", core)
        if mt:
            trail = mt.group(0)
            core = core[: mt.start()]
    ml = re.match(r"^[\"'\(\[]+", core)
    if ml:
        lead = ml.group(0)
        core = core[ml.end():]
    return core, lead, trail


def tokenize(text):
    """Split into tokens, peeling leading/trailing punctuation we may need to manage.

    We keep apostrophes, internal characters intact. Leading/trailing punctuation
    that we may need to strip in Rule 9 (commas, periods that aren't part of a
    number/abbrev/domain) are peeled into lead/trail so rule logic sees clean cores.
    """
    tokens = []
    for i, m in enumerate(text.split(" ")):
        # We split on single spaces to preserve word boundaries simply; the spec
        # examples are space-separated. Empty strings (from double spaces) become
        # empty cores we drop later.
        raw = m
        if raw == "":
            continue
        core, lead, trail = _peel_token(raw)
        tokens.append(Token(core, lead=lead, trail=trail, space_before=(i != 0)))
    return tokens


# ---------------------------------------------------------------------------
# Rule 9 (dictionary) data
# ---------------------------------------------------------------------------

# Multi-word dictionary phrases -> locked output. Matched on lowercased core sequence.
DICT_PHRASES = [
    (["one", "on", "one"], ["1-on-1"]),
    (["one", "shot"], ["one", "shot"]),  # locks "one" as a word; outputs unchanged
]

# Single-token dictionary entries: lowercased core -> locked output text.
DICT_SINGLE = {
    "company.com": "company.com",
    "ai": "AI",
    "roi": "ROI",
    "cac": "CAC",
    "ltv": "LTV",
    "ebitda": "EBITDA",
    "kpi": "KPI",
    "kpis": "KPIs",
    "cta": "CTA",
    "ctas": "CTAs",
    "b2b": "B2B",
    "b2c": "B2C",
    "ceo": "CEO",
    "cfo": "CFO",
    "sop": "SOP",        # spec Rule 1 acronym list (was missing -> 'sop' left lowercase)
    "sops": "SOPs",
    "u.s.": "U.S.",      # spec Rule 1 initialism; lowercase input must capitalize
    # camelCase brands — simple capitalization can't produce the right form
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "linkedin": "LinkedIn",
}

# Acronyms / proper output forms already-correct, used by capitalization + idempotency.
ACRONYMS = {
    "AI", "ROI", "CEO", "CFO", "SOP", "CAC", "LTV", "EBITDA", "KPI", "KPIs",
    "CTA", "CTAs", "B2B", "B2C", "CRM", "MRR", "ARR",   # CRM/MRR added 2026-06-16 (Operator L3 notes)
    "AOV",                                              # Average Order Value — added 2026-06-16 (Operator L3 notes)
}

# Proper-noun words (capitalized forms) — for capitalization rule.
PROPER_WORDS = {
    "company.com", "U.S.", "Gandhi",
    # Common public figures / brands — correct casing in captions. Add your own brand
    # names + the people you mention here so the captioner cases them correctly.
    "Elon", "Musk", "Warren", "Buffett", "Vegas",
    "Amazon", "Netflix", "Google",
    "Mr", "MrBeast", "Beast", "Roy", "Sutherland",
    "IRL", "UK",
    "I",   # the pronoun must always be capital
}


# STT misspellings -> canonical form. Add your own recurring Whisper mishearings here
# (e.g. "jon": "John") so the captioner auto-corrects them.
MISSPELLINGS = {
}

# Your own names: brand/caption-style/names.json (or $VIBE_BRAND/caption-style/names.json).
# Kept out of this file so the kit stays white-label. Shape (every key optional):
#   {"proper":      ["Shane", "Aronson"],                 # capitalize these words anywhere
#    "phrases":     {"boca raton": "Boca Raton"},         # exact multi-word output, locked
#    "words":       {"serhant": "SERHANT."},              # exact single-word output, locked
#    "misspellings": {"aaronson": "Aronson"}}             # common transcription mishearings
def _brand_names_path():
    import os
    from pathlib import Path
    env = os.environ.get("VIBE_BRAND")
    cands = [Path(env) / "caption-style" / "names.json"] if env else []
    # this file is <repo>/plugins/vibe-editing/skills/caption-clips/scripts/; brand/ sits at <repo>
    cands.append(Path(__file__).resolve().parents[5] / "brand" / "caption-style" / "names.json")
    return next((c for c in cands if c.is_file()), None)


def _load_brand_names():
    import json
    p = _brand_names_path()
    if p is None:
        return
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        print(f"spice_format: ignoring {p}: {e}", file=sys.stderr)
        return
    PROPER_WORDS.update(d.get("proper", []))
    for k, v in d.get("words", {}).items():
        DICT_SINGLE[k.lower()] = v
    for k, v in d.get("phrases", {}).items():
        DICT_PHRASES.append((k.lower().split(), v.split()))
    for k, v in d.get("misspellings", {}).items():
        MISSPELLINGS[k.lower()] = v


_load_brand_names()

# Case-insensitive lookup table built from PROPER_WORDS.
# Used by apply_capitalization so "elon" -> "Elon", "vegas" -> "Vegas", etc.
# (The dict check `core in PROPER_WORDS` is case-sensitive and misses lowercase inputs.)
_PROPER_LOWER = {w.lower(): w for w in PROPER_WORDS}

# Lowercase-locked abbreviations.
LOWER_ABBREV = {"etc.", "e.g.", "i.e.", "etc", "e.g", "i.e"}


# ---------------------------------------------------------------------------
# Number-word vocabulary
# ---------------------------------------------------------------------------

ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
SCALES = {
    "hundred": 100,
    "thousand": 1000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
    "trillion": 1_000_000_000_000,
}

MONEY_WORDS = {"dollar", "dollars", "grand"}


def _hyphen_num(w):
    """'twenty-five' -> 25 (STT often hyphenates tens-ones). None for anything else."""
    m = re.fullmatch(r"([a-z]+)-([a-z]+)", w)
    if m and m.group(1) in TENS and m.group(2) in ONES and 0 < ONES[m.group(2)] < 10:
        return TENS[m.group(1)] + ONES[m.group(2)]
    return None

# Spoken-money SHORTHAND scale units: a numeric quantity spoken immediately before one
# of these slang/abbreviated units is money written compactly with a symbol + suffix
# (e.g. "50 k" -> "$50K", "3 mil" -> "$3M", "1.5 grand" -> "$1.5K"). These are distinct
# from the full number-words "thousand"/"million", which keep their plain digit-expansion
# semantics (e.g. "two million" -> "2,000,000") unless an explicit "dollars" / money
# trigger is present. Mapping unit-word -> compact suffix.
MONEY_UNIT_SUFFIX = {
    "k": "K",
    "grand": "K",
    "mil": "M",
    "m": "M",
}

# Rate units (Rule 5 implied-money rate phrase).
RATE_UNITS = {"hour", "day", "week", "month", "quarter", "year"}

# Counting nouns that block money (Rule 5 guard #2). Stored singular; plurals handled.
COUNTING_NOUNS = {
    "year", "years", "month", "months", "week", "weeks", "day", "days",
    "hour", "hours", "minute", "minutes", "point", "points", "people",
    "person", "customer", "customers", "client", "clients", "editor",
    "editors", "employee", "employees", "rep", "reps", "user", "users",
    "member", "members",
}

# Reliable money trigger words (Rule 5 #4). Stored as a set of lemma-ish strings;
# we match by prefix for charge/charging/charged.
MONEY_TRIGGERS = {
    "money", "cash", "cashflow", "revenue", "profit", "ebitda", "pricing",
    "price", "cost", "costs", "salary", "fee", "fees", "charge", "charges",
    "charging", "charged", "budget", "expense", "expenses", "income",
    "earnings", "wage", "wages", "raise", "rate", "rates",
}

# Context-neutral words that do NOT trigger money alone.
NEUTRAL_WORDS = {"deal", "deals", "sale", "sales", "payment", "payments",
                 "invoice", "invoices", "quote", "quotes", "bonus", "bonuses"}

# Sequence list-item words -> "#N".
SEQ_HASH = {
    "step", "tip", "rule", "law", "point", "reason", "principle", "habit",
    "mistake", "tactic", "strategy", "lesson", "factor", "secret", "key",
}
# Sequence time/stage words -> plain "N".
SEQ_PLAIN = {"day", "week", "month", "round", "level", "season"}

# Rule-4 trigger words that KEEP "one" as a word.
KEEP_ONE_PREV = {
    "and", "but", "or", "so", "yet", "nor", "if", "when", "while", "because",
    "although", "though", "since", "unless", "after", "before", "until", "as",
    "once", "whereas", "whether", "the", "a", "an", "well", "now", "look", "okay",
    # pronoun/idiom formers — "one" here is NEVER a count ("no one will care",
    # "every one of them", "which one", "the wrong one"). Shipped "no 1 will care"
    # on a delivered clip 2026-06-12 — user: "don't make this mistake again".
    "no", "every", "any", "some", "each", "which", "this", "that", "only",
    "wrong", "right", "loved", "another",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def lc(s):
    return s.lower()


def is_pure_int_digits(s):
    """True if s is an integer token of digits, optionally with thousands commas."""
    return bool(re.fullmatch(r"\d{1,3}(,\d{3})*|\d+", s))


def parse_int_token(s):
    """Return int value of a digit token (commas allowed), else None."""
    if re.fullmatch(r"\d{1,3}(,\d{3})*", s) or re.fullmatch(r"\d+", s):
        return int(s.replace(",", ""))
    return None


def add_thousands(n):
    """Format integer with thousands separators."""
    return f"{n:,}"


def _num_for_suffix(value, prev_tok):
    """Render the numeric part that precedes a % or X suffix, applying Rule-8 thousands
    separators when the value was SPOKEN as words and is >= 1,000 (the suffix is about
    to lock the token, so Rule 8 can't run on it afterward)."""
    if getattr(prev_tok, "spoken", False) and value >= 1000:
        return add_thousands(value)
    return str(value)


# Patterns for tokens already in correct OUTPUT form (idempotency — leave untouched).
_FMT_PATTERNS = [
    re.compile(r"^\$\d{1,3}(,\d{3})*$"),                 # $0 / $500 / $1,000 / $5,000
    re.compile(r"^\$\d+(\.\d+)?[KM]$"),                  # $1K / $50K / $1.2M / $100M
    re.compile(r"^\$\d+(\.\d+)?(?: Billion| Trillion)$"),  # $2 Billion / $1.5 Trillion (format_money_value output)
    re.compile(r"^\$\d+(\.\d+)?$"),                      # $100
    re.compile(r"^\d{1,3}(,\d{3})*(\.\d+)?X('?\w*)?$"),  # 10X / 10X'ing / 2X / 2,000X
    re.compile(r"^\d+%$"),                               # 50% / 0%
    re.compile(r"^#\d+$"),                               # #1 / #2
    re.compile(r"^etc\.$|^e\.g\.$|^i\.e\.$"),            # lowercase abbreviations
    re.compile(r"^(?:[A-Z]\.){2,}$"),                    # U.S.
]
# "$N Billion" / "$N Trillion" handled specially (two tokens) below.


def _is_already_formatted(text):
    for pat in _FMT_PATTERNS:
        if pat.fullmatch(text):
            return True
    return False


# A "$N" / "$N.N" money amount, optionally with one leading word/article fused onto it by
# the STT tokenizer ("a $400", "the $5", "an $8") — Whisper frequently glues the article
# onto the money word as a single token. The amount must sit at the token start or right
# after a space, so we never pull a "$N" out of the middle of a malformed fragment.
_MONEY_SCALE_BASE_RE = re.compile(r"^(\S+\s+)?\$(\d+(?:\.\d+)?)$")

# A money token already in TERMINAL form (suffix K/M, or spelled-out Billion/Trillion) with
# one leading word/article glued on ("a $400M", "an $8 Billion") — the exact shape produced
# when an article-glued "$N" amount folds with a scale word. Re-locking it on a second pass
# stops capitalization from lowercasing the suffix ("$400M"->"$400m"), preserving idempotency.
_GLUED_TERMINAL_MONEY_RE = re.compile(r"^\S+\s+\$\d+(?:\.\d+)?(?:[KM]| Billion| Trillion)$")


def _money_scale_base(text):
    """If `text` is a '$N' / '$N.N' money amount (optionally with one leading word/article
    glued on, e.g. 'a $400'), return (prefix, num_str); otherwise None.

    The three rules that fold a money amount into a following scale word
    ("$400 million" -> "$400M") use this so the fold still fires when the STT glued an
    article onto the amount ("a $400") or the amount carries a decimal ("$1.5"). Without
    it the scale word falls through to number-word expansion and becomes "1,000,000".
    """
    m = _MONEY_SCALE_BASE_RE.match(text)
    if not m:
        return None
    return (m.group(1) or ""), m.group(2)


# ---------------------------------------------------------------------------
# Step 0: STT misspelling correction (runs BEFORE apply_dictionary)
# ---------------------------------------------------------------------------

def apply_misspellings(tokens):
    """Correct confirmed STT misspellings before dictionary/rule processing.

    Does NOT lock tokens — corrected text flows through all downstream rules
    normally. Specifically: a misspelling -> its canonical form (unlocked) so the phrase dict
    can still match ["creator", "creator"] -> "the creator".
    """
    for tok in tokens:
        if not tok.locked:
            w = lc(tok.text)
            if w in MISSPELLINGS:
                tok.text = MISSPELLINGS[w]
    return tokens


# ---------------------------------------------------------------------------
# Rule 1: Dictionary (FIRST, authoritative)
# ---------------------------------------------------------------------------

def apply_dictionary(tokens):
    out = []
    i = 0
    n = len(tokens)
    while i < n:
        matched = False
        # Try multi-word phrases (longest first by list order; check all, prefer longest)
        # Sort phrases by length desc to prefer 3-word over 2-word.
        for phrase, repl in sorted(DICT_PHRASES, key=lambda p: -len(p[0])):
            plen = len(phrase)
            if i + plen <= n:
                seq = [lc(tokens[i + k].text) for k in range(plen)]
                if seq == phrase and all(not tokens[i + k].locked for k in range(plen)):
                    # Build replacement tokens. Preserve lead of first, trail of last.
                    lead = tokens[i].lead
                    trail = tokens[i + plen - 1].trail
                    space_before = tokens[i].space_before
                    merged_src = _merge_src(*[tokens[i + k] for k in range(plen)])
                    for j, w in enumerate(repl):
                        t = Token(w,
                                  lead=lead if j == 0 else "",
                                  trail=trail if j == len(repl) - 1 else "",
                                  space_before=space_before if j == 0 else True,
                                  src=merged_src)
                        t.locked = True
                        out.append(t)
                    i += plen
                    matched = True
                    break
        if matched:
            continue
        tok = tokens[i]
        # Single-token dictionary
        key = lc(tok.text)
        if key in DICT_SINGLE and not tok.locked:
            tok.text = DICT_SINGLE[key]
            tok.locked = True
        # Idempotency: already-correct acronym/format tokens lock themselves.
        elif tok.text in ACRONYMS:
            tok.locked = True
        elif tok.text == "1-on-1":
            tok.locked = True
        elif tok.text == "company.com":
            tok.locked = True
        elif _is_already_formatted(tok.text):
            # Don't lock a "$N" amount if a scale word follows — it must recombine
            # ("$100 million" -> $100M, "$1.5 billion" -> $1.5 Billion) in the money pass
            # first. _money_scale_base also accepts a decimal amount ("$1.5").
            nxt_w = lc(tokens[i + 1].text) if i + 1 < n else ""
            if _money_scale_base(tok.text) and nxt_w in SCALES:
                pass
            else:
                tok.locked = True
        elif _GLUED_TERMINAL_MONEY_RE.match(tok.text):
            # Idempotency: a 2nd pass sees a glued terminal-money token ("a $400M") whole
            # (one word) — lock it so capitalization can't lowercase the suffix.
            tok.locked = True
        out.append(tok)
        i += 1
    return out


# ---------------------------------------------------------------------------
# Rule 2 (impl as 3): Number words -> digits, incl. compound numbers
# ---------------------------------------------------------------------------

def _collect_number_run(tokens, i):
    """Starting at index i, greedily collect a run of number words that compose a
    single numeric value. Returns (value, end_index, used)
    or None if tokens[i] is not a number word.

    Handles: ones/teens, tens [+ one], scales (hundred/thousand/million/...),
    compound: "fifty thousand", "one hundred thousand", "three hundred million",
    "five hundred", "two million".
    Also accepts a LEADING bare-digit token followed by a scale word, e.g.
    "44 million", "200 million", "5 thousand" -> combines digit * scale.
    Does NOT consume across non-number words.
    """
    n = len(tokens)
    j = i
    total = 0          # accumulated finished groups
    current = 0        # current group being built (below 1000)
    used = 0           # how many number words consumed
    saw_number = False

    # Allow a leading bare-digit token to seed `current` IF followed by a scale word.
    if j < n and not tokens[j].locked:
        dv = parse_int_token(tokens[j].text)
        if dv is not None:
            nxt_w = lc(tokens[j + 1].text) if (j + 1 < n and not tokens[j + 1].locked) else ""
            if nxt_w in SCALES:
                current = dv
                saw_number = True
                used += 1
                j += 1
            else:
                return None  # bare digit not followed by scale: not our job

    while j < n:
        if tokens[j].locked:
            break
        w = lc(tokens[j].text)
        if w in ONES:
            if used and current == 0 and total == 0 and saw_number:
                # e.g. "fifty thousand five" -> would need additive; spec defers.
                break
            current += ONES[w]
            saw_number = True
            used += 1
            j += 1
        elif _hyphen_num(w) is not None:
            current += _hyphen_num(w)
            saw_number = True
            used += 1
            j += 1
        elif w in TENS:
            current += TENS[w]
            saw_number = True
            used += 1
            j += 1
            # allow "twenty two"
            if j < n and not tokens[j].locked and lc(tokens[j].text) in ONES and ONES[lc(tokens[j].text)] < 10 and lc(tokens[j].text) != "zero":
                current += ONES[lc(tokens[j].text)]
                used += 1
                j += 1
        elif w == "hundred":
            if current == 0:
                current = 1
            current *= 100
            saw_number = True
            used += 1
            j += 1
        elif w in ("thousand", "million", "billion", "trillion"):
            mult = SCALES[w]
            if current == 0:
                current = 1
            total += current * mult
            current = 0
            saw_number = True
            used += 1
            j += 1
            # stop after a big scale unless followed by another scale chain we already handle
            # (the "hundred thousand" case is handled because hundred set current first)
        else:
            break

    if not saw_number:
        return None
    value = total + current
    return value, j, used


_DEC_RE = re.compile(r"\d+\.\d+")
_DEC_FOLLOW = {"thousand", "million", "billion", "trillion", "percent", "percentage"}


def _is_num_word(w):
    return w in ONES or w in TENS or w == "hundred" or _hyphen_num(w) is not None


def apply_spoken_decimals(tokens):
    """'two point three million' -> '2.3 million'; 'two point five percent' -> '2.5 percent'.

    Only when the decimal is followed by a scale word or percent, so 'at one point three
    people left' is left alone. Runs before apply_number_words, which then folds the
    decimal into the scale ('2.3 million' -> 2300000)."""
    out = []
    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]
        if (not tok.locked and lc(tok.text) == "point" and out):
            # integer part: the number-word run (or a digit token) right before "point"
            s = len(out)
            while s > 0 and not out[s - 1].locked and _is_num_word(lc(out[s - 1].text)):
                s -= 1
            int_val = None
            if s < len(out):
                run = _collect_number_run(out, s)
                if run is not None and run[1] == len(out):
                    int_val = run[0]
            elif out and not out[-1].locked and parse_int_token(out[-1].text) is not None:
                s, int_val = len(out) - 1, parse_int_token(out[-1].text)
            # fractional digits: single digit words after "point"
            j, frac = i + 1, ""
            while j < n and not tokens[j].locked and lc(tokens[j].text) in ONES \
                    and ONES[lc(tokens[j].text)] < 10:
                frac += str(ONES[lc(tokens[j].text)])
                j += 1
            if int_val is not None and frac and j < n and lc(tokens[j].text) in _DEC_FOLLOW:
                consumed = out[s:] + tokens[i:j]
                newtok = Token(f"{int_val}.{frac}", lead=consumed[0].lead, trail=tokens[j - 1].trail,
                               space_before=consumed[0].space_before, spoken=True,
                               src=_merge_src(*consumed))
                out = out[:s] + [newtok]
                i = j
                continue
        out.append(tok)
        i += 1
    return out


def apply_number_words(tokens):
    """Convert number-word runs into a single digit token. Compound numbers combine.

    Special handling:
      - "a" before a scale word ("a thousand") -> behaves like "1" but in non-money
        context spec wants "a 1,000" (article kept). We keep "a" as its own token and
        convert the scale-run after it. Money rules later may consume the "a".
      - leave lone "one" tokens as the WORD for Rule 4 to decide; but if "one" is part
        of a larger compound (one hundred, one thousand) it IS consumed here.
      - "zero" stays "zero" unless directly followed by a money/percent word (handled
        partly here: we leave zero as word; money rule converts).
    """
    out = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok.locked:
            out.append(tok)
            i += 1
            continue
        w = lc(tok.text)

        # Lone "one": defer to Rule 4 unless it begins a compound (one hundred/thousand/...).
        if w == "one":
            nxt = lc(tokens[i + 1].text) if i + 1 < n and not tokens[i + 1].locked else ""
            if nxt in ("hundred", "thousand", "million", "billion", "trillion"):
                pass  # fall through to compound collection
            else:
                out.append(tok)  # keep word; Rule 4 decides
                i += 1
                continue

        # "zero": keep as word here; money/percent conversion handled in Rule 6/5.
        if w == "zero":
            out.append(tok)
            i += 1
            continue

        # Lone scale word immediately after a "$N" amount: leave for the money pass to
        # recombine (e.g. "$100 million" -> $100M). _money_scale_base also recognizes an
        # STT-glued-article amount ("a $400") and a decimal ("$1.5"), so the scale word
        # never falls through here and expands to 1,000,000.
        if w in SCALES and out:
            prev = out[-1]
            if _money_scale_base(prev.text):
                out.append(tok)
                i += 1
                continue
            # Decimal quantity + scale: "2.3 million" -> 2300000 (was "2.3 1,000,000").
            if w != "hundred" and not prev.locked and _DEC_RE.fullmatch(prev.text):
                val = Decimal(prev.text) * SCALES[w]
                if val == val.to_integral_value():
                    prev.text = str(int(val))
                    prev.spoken = True
                    prev.trail = tok.trail
                    prev.src = _merge_src(prev, tok)
                    i += 1
                    continue

        run = _collect_number_run(tokens, i)
        if run is not None:
            value, end, _used = run
            # Build a single digit token. No comma yet (Rule 7 adds it / money rule).
            # spoken=True ONLY when at least one true number WORD was used (a leading
            # bare-digit seed like "5000" stays non-spoken; "5 thousand" becomes spoken).
            seeded_by_bare_digit = parse_int_token(tokens[i].text) is not None
            produced_spoken = (not seeded_by_bare_digit) or (end - i) > 1
            newtok = Token(str(value),
                           lead=tokens[i].lead,
                           trail=tokens[end - 1].trail,
                           space_before=tokens[i].space_before,
                           spoken=produced_spoken,
                           src=_merge_src(*tokens[i:end]))
            out.append(newtok)
            i = end
            continue

        out.append(tok)
        i += 1
    return out


# ---------------------------------------------------------------------------
# Rule 3 (impl): "one" vs "1"
# ---------------------------------------------------------------------------

def apply_one_vs_1(tokens):
    out = []
    n = len(tokens)
    for i, tok in enumerate(tokens):
        if tok.locked:
            out.append(tok)
            continue
        if lc(tok.text) != "one":
            out.append(tok)
            continue
        # Decide.
        prev = None
        for k in range(i - 1, -1, -1):
            prev = tokens[k]
            break
        nxt = tokens[i + 1] if i + 1 < n else None
        prev_w = lc(prev.text) if prev is not None else None
        nxt_w = lc(nxt.text) if nxt is not None else None

        # Next word forces "1": money word or another number.
        next_is_money = nxt_w in MONEY_WORDS if nxt_w else False
        next_is_number = bool(nxt and (is_pure_int_digits(nxt.text) or lc(nxt.text) in ONES or lc(nxt.text) in TENS or lc(nxt.text) in SCALES)) if nxt else False
        if next_is_money or next_is_number:
            tok.text = "1"
            out.append(tok)
            continue

        # DEFAULT = KEEP THE WORD. In conversational speech a bare "one" is overwhelmingly the
        # pronoun / article — "no one", "one of the most", "the one thing", "is one of", "become
        # one", "which one" — NOT a digit-worthy count. The ONLY paths to a numeral are the
        # positive signals above (money / adjacent number, e.g. "one hundred" handled upstream) and
        # the listicle labels "number/step/day one" -> "#1", which apply_listicle_sequence handles
        # DOWNSTREAM by matching the WORD "one" (so keeping the word here does not break "#1").
        #
        # This replaces the old "default to 1" which shipped THREE numeral bugs in one batch
        # (2026-06-12: "no 1 will care", "judgment is 1") — every one a pronoun mis-read as a count.
        # KEEP_ONE_PREV / prev / nxt are left computed for clarity but no longer gate the keep.
        _ = (prev_w, nxt_w, KEEP_ONE_PREV)  # (kept for readability; default is now KEEP)
        out.append(tok)
    return out


# ---------------------------------------------------------------------------
# Rule 4 (impl): Listicle & sequence labels
# ---------------------------------------------------------------------------

def apply_listicle_sequence(tokens):
    out = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok.locked:
            out.append(tok)
            i += 1
            continue
        w = lc(tok.text)

        # Listicle: "number" directly precedes a numeral -> "#N"
        if w == "number" and i + 1 < n:
            nxt = tokens[i + 1]
            val = parse_int_token(nxt.text)
            nxt_w = lc(nxt.text)
            if val is None and nxt_w in ONES:
                val = ONES[nxt_w]
            if val is not None:
                newtok = Token(f"#{val}",
                               lead=tok.lead,
                               trail=nxt.trail,
                               space_before=tok.space_before,
                               src=_merge_src(tok, nxt))
                newtok.locked = True
                out.append(newtok)
                i += 2
                continue

        # Sequence labels: list-item word followed by a numeral.
        if (w in SEQ_HASH or w in SEQ_PLAIN) and i + 1 < n:
            nxt = tokens[i + 1]
            val = parse_int_token(nxt.text)
            nxt_w = lc(nxt.text)
            if val is None and nxt_w in ONES:
                val = ONES[nxt_w]
            if val is not None:
                # keep the label word, replace number token
                out.append(tok)
                if w in SEQ_HASH:
                    newtok = Token(f"#{val}", lead="", trail=nxt.trail, space_before=True,
                                   src=_merge_src(nxt))
                else:
                    newtok = Token(str(val), lead="", trail=nxt.trail, space_before=True,
                                   src=_merge_src(nxt))
                newtok.locked = True
                out.append(newtok)
                i += 2
                continue

        out.append(tok)
        i += 1
    return out


# ---------------------------------------------------------------------------
# Rule 5 (impl): Money formatting
# ---------------------------------------------------------------------------

def format_money_value(value):
    """Format an integer dollar value per the money table.

    $1-$999 -> $N
    $1,000-$19,999 -> $N,000  (full-phrase default)
    $20,000-$999,999 -> $NK
    $1M-$999.9M -> $NM
    $1B-$999.9B -> $N Billion
    $1T-$999.9T -> $N Trillion
    """
    if value == 0:
        return "$0"
    if value < 1000:
        return f"${value}"
    if value < 20000:
        return f"${add_thousands(value)}"
    if value < 1_000_000:
        k = value / 1000
        return f"${_trim(k)}K"
    if value < 1_000_000_000:
        m = value / 1_000_000
        return f"${_trim(m)}M"
    if value < 1_000_000_000_000:
        b = value / 1_000_000_000
        return f"${_trim(b)} Billion"
    t = value / 1_000_000_000_000
    return f"${_trim(t)} Trillion"


def format_money_shorthand_k(value):
    """$NK form for $1,000-$19,999 spoken shorthand (1k/5k/10k)."""
    if value < 1000:
        return f"${value}"
    k = value / 1000
    return f"${_trim(k)}K"


def _trim(x):
    """Trim trailing .0 from a float; keep one decimal otherwise."""
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.1f}".rstrip("0").rstrip(".")


# Numeric quantity that may carry a decimal point (e.g. "1.5", "3.5", "100").
_NUM_QTY_RE = re.compile(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?")


def _parse_qty_token(text):
    """Parse a bare numeric quantity token (integer or decimal, commas allowed) into a
    normalized numeric STRING for compact-money rendering (commas stripped, trailing .0
    dropped). Returns None if the token isn't a clean standalone quantity.

    Used by the spoken-money SHORTHAND pass so "1.5"/"3.5"/"100" all render compactly
    ("1.5", "3.5", "100") in "$<n>K" / "$<n>M". Conservative: the WHOLE token must be the
    number, so symbols-suffixed tokens ("$5", "10X", "#1") never match here.
    """
    if not _NUM_QTY_RE.fullmatch(text):
        return None
    raw = text.replace(",", "")
    try:
        float(raw)
    except ValueError:
        return None
    # Render: keep the spoken precision exactly (".25" must survive). Strip a redundant
    # trailing ".0"/".00" and any trailing zeros on a real fraction; never round.
    if "." in raw:
        intpart, frac = raw.split(".", 1)
        frac = frac.rstrip("0")
        return intpart if frac == "" else f"{intpart}.{frac}"
    return str(int(raw))


def _value_from_number_token(tok):
    """Get integer value from a (possibly digit, possibly K-shorthand) token."""
    v = parse_int_token(tok.text)
    if v is not None:
        return v, False  # (value, was_shorthand_k)
    m = re.fullmatch(r"(\d+)k", lc(tok.text))
    if m:
        return int(m.group(1)) * 1000, True
    m = re.fullmatch(r"\$?(\d+)k", lc(tok.text))
    if m:
        return int(m.group(1)) * 1000, True
    return None, False


# Money-unit words a collapsed word-fraction ("three and a half" -> 3.5) is allowed to
# precede. Gated to the SLANG money shorthands only, so the collapse can ONLY fire in an
# unambiguous money context — it never rewrites "an hour and a half" / "three and a half
# years", and it never touches the full-word "million"/"thousand" (which keep their plain
# digit-expansion semantics, e.g. "three million" -> 3,000,000, unless an explicit
# "dollars"/trigger is present).
_FRACTION_MONEY_UNITS = {"k", "mil", "m", "grand"}
# Spoken word-fraction tails -> decimal STRING addend. Common cases only (task scope).
_FRACTION_TAILS = {
    ("and", "a", "half"): "0.5",
    ("and", "a", "quarter"): "0.25",
}


def _fraction_decimal(base_int, tail_str):
    """Render an integer + a spoken fraction tail ("0.5"/"0.25") as a clean decimal string
    with no trailing-zero loss (e.g. 3 + '0.5' -> '3.5', 5 + '0.25' -> '5.25')."""
    from decimal import Decimal
    d = Decimal(base_int) + Decimal(tail_str)
    s = format(d.normalize(), "f")
    return s


def apply_money(tokens):
    """Apply money formatting. Handles:
       - word-number fractions in a money context ("three and a half mil" -> 3.5 mil)
       - explicit dollars/dollar after a number -> $ formatting
       - "$N" + scale word recombination
       - grand and spoken shorthand units (k / mil / m)
       - implied money (rate phrase, very large, trigger words) with guards
       - year guard
       - zero dollars/percent
    """
    n = len(tokens)

    # Pass 0: word-number FRACTION collapse, money-gated.
    # "<int> and a half|quarter <money-unit>" -> "<int>.5|.25 <money-unit>". Conservative:
    # the integer must be a clean number token, the tail must be exactly "and a half" /
    # "and a quarter", and a money unit (k / mil / m / grand / thousand / million / ...)
    # must immediately follow — so non-money phrases ("three and a half years") are
    # untouched. The resulting decimal is consumed by the shorthand/scale money passes
    # below ("3.5 mil" -> "$3.5M").
    out = []
    i = 0
    while i < n:
        tok = tokens[i]
        if tok.locked:
            out.append(tok)
            i += 1
            continue
        base = parse_int_token(tok.text)
        if base is not None and i + 4 < len(tokens):
            tail = tuple(lc(tokens[i + k].text) for k in range(1, 4))
            unit = lc(tokens[i + 4].text)
            if tail in _FRACTION_TAILS and unit in _FRACTION_MONEY_UNITS \
                    and all(not tokens[i + k].locked for k in range(1, 4)):
                dec_str = _fraction_decimal(base, _FRACTION_TAILS[tail])
                newtok = Token(dec_str, lead=tok.lead, trail=tokens[i + 3].trail,
                               space_before=tok.space_before,
                               spoken=True,
                               src=_merge_src(*tokens[i:i + 4]))
                out.append(newtok)
                i += 4
                continue
        out.append(tok)
        i += 1
    tokens = out
    n = len(tokens)

    # Precompute lowercased cores for context scanning.
    cores = [lc(t.text) for t in tokens]

    # Pass A: explicit "<number> dollar(s)" and "zero dollars"
    out = []
    i = 0
    while i < n:
        tok = tokens[i]
        if tok.locked:
            out.append(tok)
            i += 1
            continue
        w = lc(tok.text)

        # zero dollars -> $0
        if w == "zero" and i + 1 < n and lc(tokens[i + 1].text) in ("dollar", "dollars"):
            newtok = Token("$0", lead=tok.lead, trail=tokens[i + 1].trail, space_before=tok.space_before,
                           src=_merge_src(tok, tokens[i + 1]))
            newtok.locked = True
            out.append(newtok)
            i += 2
            continue
        # zero percent / percentage -> handled in Rule 6, but convert zero->0 there.

        # "$N" + scale word  (e.g. "$100 million", "$5 thousand", "$2 billion").
        # _money_scale_base also matches an STT-glued-article amount ("a $400 million") and
        # a decimal amount ("$1.5 billion"); any glued leading word is preserved verbatim on
        # the folded token ("a $400" + "million" -> "a $400M").
        msb = _money_scale_base(tok.text)
        if msb and i + 1 < n and lc(tokens[i + 1].text) in ("thousand", "million", "billion", "trillion"):
            prefix, num = msb
            scale = SCALES[lc(tokens[i + 1].text)]
            value = int(Decimal(num) * scale)  # exact; scale >= 1000 so always whole
            newtok = Token(prefix + format_money_value(value), lead=tok.lead,
                           trail=tokens[i + 1].trail, space_before=tok.space_before,
                           src=_merge_src(tok, tokens[i + 1]))
            newtok.locked = True
            out.append(newtok)
            i += 2
            continue

        # number token followed by dollar(s)
        val, was_k = _value_from_number_token(tok)
        if val is not None and i + 1 < n and lc(tokens[i + 1].text) in ("dollar", "dollars"):
            # disambiguation: shorthand k -> $NK; full phrase -> $N,000
            if 1000 <= val < 20000 and was_k:
                txt = format_money_shorthand_k(val)
            else:
                txt = format_money_value(val)
            newtok = Token(txt, lead=tok.lead, trail=tokens[i + 1].trail, space_before=tok.space_before,
                           src=_merge_src(tok, tokens[i + 1]))
            newtok.locked = True
            out.append(newtok)
            i += 2
            continue

        out.append(tok)
        i += 1
    tokens = out
    n = len(tokens)

    # Pass A2: spoken-money SHORTHAND units  "<qty> k|mil|m" -> "$<qty>K" / "$<qty>M".
    # Conservative: fires ONLY when a CLEAN standalone numeric quantity (integer or
    # decimal, e.g. "50", "3", "1.5") sits immediately before the unit token. The unit
    # must be exactly "k" / "mil" / "m" (slang shorthands that imply money in this
    # domain). Bare full-words "thousand" / "million" are intentionally NOT money-ized
    # here — they keep their plain digit-expansion semantics ("two million" -> 2,000,000)
    # and only become money via an explicit "dollars" / money-trigger elsewhere.
    out = []
    i = 0
    while i < n:
        tok = tokens[i]
        if tok.locked:
            out.append(tok)
            i += 1
            continue
        w = lc(tok.text)
        if w in ("k", "mil", "m") and out:
            prev = out[-1]
            qty = None if prev.locked else _parse_qty_token(prev.text)
            if qty is not None:
                suffix = MONEY_UNIT_SUFFIX[w]
                consumed = out.pop()  # the quantity token
                newtok = Token(f"${qty}{suffix}", lead=consumed.lead,
                               trail=tok.trail or consumed.trail,
                               space_before=consumed.space_before,
                               src=_merge_src(consumed, tok))
                newtok.locked = True
                out.append(newtok)
                i += 1
                continue
        out.append(tok)
        i += 1
    tokens = out
    n = len(tokens)

    # Pass B: "grand" handling.  <number|a|one> grand
    out = []
    i = 0
    while i < n:
        tok = tokens[i]
        if tok.locked:
            out.append(tok)
            i += 1
            continue
        w = lc(tok.text)
        if w == "grand":
            # DECIMAL quantity before "grand" ("1.5 grand") -> compact "$1.5K". A whole-
            # number grand still routes through the integer path below (so the money table
            # + article-dropping for "a hundred grand" stay intact).
            if out and not out[-1].locked:
                pdec = _parse_qty_token(out[-1].text)
                if pdec is not None and "." in pdec:
                    consumed = out.pop()
                    newtok = Token(f"${pdec}K", lead=consumed.lead,
                                   trail=tok.trail or consumed.trail,
                                   space_before=consumed.space_before,
                                   src=_merge_src(consumed, tok))
                    newtok.locked = True
                    out.append(newtok)
                    i += 1
                    continue
            # look back at previous emitted token for the multiplier
            mult = None
            consumed_prev = False
            if out:
                prev = out[-1]
                pv = parse_int_token(prev.text)
                pw = lc(prev.text)
                if pv is not None and not prev.locked:
                    mult = pv
                    consumed_prev = True
                elif pw in ("a", "one") and not prev.locked:
                    mult = 1
                    consumed_prev = True
                elif pw in ONES and not prev.locked:
                    mult = ONES[pw]
                    consumed_prev = True
            if mult is None:
                mult = 1
            value = mult * 1000
            consumed_toks = [tok]
            if consumed_prev:
                consumed_toks.insert(0, out.pop())
                # If an article "a"/"an" precedes the consumed number ("a hundred grand"),
                # it was the "one" article -> drop it.
                if out and lc(out[-1].text) in ("a", "an") and not out[-1].locked:
                    consumed_toks.insert(0, out.pop())
            txt = format_money_value(value)
            newtok = Token(txt, lead=(out and "" or tok.lead) or tok.lead,
                           trail=tok.trail, space_before=tok.space_before,
                           src=_merge_src(*consumed_toks))
            newtok.locked = True
            out.append(newtok)
            i += 1
            continue
        out.append(tok)
        i += 1
    tokens = out
    n = len(tokens)
    cores = [lc(t.text) for t in tokens]

    # Is the WHOLE input a single isolated number? (e.g. "three hundred million")
    # If so, suppress the very-large auto-money rule — an isolated number is a count
    # demonstration, not an amount in running speech.
    nonempty = [t for t in tokens if t.text]
    isolated_number = (len(nonempty) == 1 and parse_int_token(nonempty[0].text) is not None)

    # Pass C: implied money on bare number tokens.
    out = []
    drop_idx = set()
    for i, tok in enumerate(tokens):
        if tok.locked or i in drop_idx:
            if i not in drop_idx:
                out.append(tok)
            continue
        val = parse_int_token(tok.text)
        # Also recognize spoken-shorthand "K" tokens ("5k", "10k", "50k") so implied
        # money context can format them ("the price is 5k" -> "the price is $5K").
        is_shorthand_k = False
        if val is None:
            kv, was_k = _value_from_number_token(tok)
            if was_k:
                val = kv
                is_shorthand_k = True
        if val is None:
            out.append(tok)
            continue

        # Year guard: a bare 4-digit DIGIT literal in 1900-2099 is a year, not money.
        # Word-spoken amounts ("two thousand"->2000) are EXEMPT from the year guard
        # (spec Rule 6/8), so only pre-existing digit literals trigger it.
        is_yearish = (not tok.spoken) and bool(re.fullmatch(r"\d{4}", tok.text)) and 1900 <= val <= 2099

        money = False
        rate_drop_prev_a = False

        # 1. Rate phrase (highest): number followed by "a"/"an" + rate unit.
        if i + 2 < len(tokens) and lc(tokens[i + 1].text) in ("a", "an") \
                and lc(tokens[i + 2].text) in RATE_UNITS:
            money = True
            # the leading "a" meaning "one" before the AMOUNT is dropped (if present);
            # the rate "a" (tokens[i+1]) is KEPT.
            if out and lc(out[-1].text) in ("a", "an") and not out[-1].locked:
                rate_drop_prev_a = True

        # 2. Counting-noun guard: number directly followed by counting noun -> NOT money.
        next_core = lc(tokens[i + 1].text) if i + 1 < len(tokens) else ""
        if next_core in COUNTING_NOUNS:
            out.append(tok)
            continue

        # 3. Very large numbers: >= 100M is money regardless (unless isolated).
        if not money and val >= 100_000_000 and not isolated_number and not is_yearish:
            money = True

        # 4. Reliable trigger words within ~8 before / 3 after.
        trigger_drop_prev_a = False
        if not money and not is_yearish:
            if _has_money_trigger(cores, i, before=8, after=3) and val >= 1000:
                money = True
                # standalone money answer: "a thousand" -> "$1,000" (consume the "a").
                # Only when the amount is the standalone value (end of clause), NOT when
                # an item noun follows ("a 100 dollar deal" keeps "a").
                is_last = (i + 1 >= len(tokens)) or bool(re.search(r"[\.\?\!]", tokens[i].trail))
                if is_last and out and lc(out[-1].text) in ("a", "an") and not out[-1].locked:
                    trigger_drop_prev_a = True

        if is_yearish:
            money = False

        if money:
            consumed_toks = [tok]
            if rate_drop_prev_a or trigger_drop_prev_a:
                consumed_toks.insert(0, out.pop())  # drop the preceding article "a"/"an"
            # Shorthand-K in the $1,000-$19,999 band stays $NK ("5k"->$5K); everything
            # else (and >= $20K) follows the standard money table.
            if is_shorthand_k and 1000 <= val < 20000:
                txt = format_money_shorthand_k(val)
            else:
                txt = format_money_value(val)
            newtok = Token(txt, lead=tok.lead, trail=tok.trail, space_before=tok.space_before,
                           src=_merge_src(*consumed_toks))
            newtok.locked = True
            out.append(newtok)
            continue

        out.append(tok)
    return out


def _has_money_trigger(cores, idx, before, after):
    n = len(cores)
    lo = max(0, idx - before)
    hi = min(n, idx + after + 1)
    for k in range(lo, hi):
        if k == idx:
            continue
        c = cores[k].strip(".,!?")
        if c in MONEY_TRIGGERS:
            return True
        # charge/charging/charged prefix
        if c.startswith("charg"):
            return True
    return False


# ---------------------------------------------------------------------------
# Rule 6 (impl): Symbol & multiplier rule
# ---------------------------------------------------------------------------

def apply_symbols_multipliers(tokens):
    out = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok.locked:
            out.append(tok)
            i += 1
            continue
        w = lc(tok.text)

        # percent / percentage after a number -> N%
        # (% locks the token, so Rule-8 thousands can't run afterward — apply the comma
        #  HERE for spoken values >= 1,000: "fifty thousand percent" -> "50,000%".)
        if w in ("percent", "percentage") and out:
            prev = out[-1]
            pv = parse_int_token(prev.text)
            pw = lc(prev.text)
            if pv is None and not prev.locked and _DEC_RE.fullmatch(prev.text):  # "2.5 percent" -> 2.5%
                prev.text = f"{prev.text}%"
                prev.locked = True
                prev.trail = tok.trail or prev.trail
                prev.src = _merge_src(prev, tok)
                i += 1
                continue
            if pv is not None and not prev.locked:
                prev.text = f"{_num_for_suffix(pv, prev)}%"
                prev.locked = True
                prev.trail = tok.trail or prev.trail
                prev.src = _merge_src(prev, tok)
                i += 1
                continue
            if pw == "zero" and not prev.locked:
                prev.text = "0%"
                prev.locked = True
                prev.trail = tok.trail or prev.trail
                prev.src = _merge_src(prev, tok)
                i += 1
                continue

        # bare "%" symbol after a number (or "zero") -> N% (spec Rule 3 zero exception)
        if tok.text == "%" and out:
            prev = out[-1]
            pv = parse_int_token(prev.text)
            pw = lc(prev.text)
            if pv is not None and not prev.locked:
                prev.text = f"{_num_for_suffix(pv, prev)}%"
                prev.locked = True
                prev.trail = tok.trail or prev.trail
                prev.src = _merge_src(prev, tok)
                i += 1
                continue
            if pw == "zero" and not prev.locked:
                prev.text = "0%"
                prev.locked = True
                prev.trail = tok.trail or prev.trail
                prev.src = _merge_src(prev, tok)
                i += 1
                continue

        # bare "$" symbol after "zero" -> $0 (spec Rule 3 zero exception: '$' trigger)
        if tok.text == "$" and out:
            prev = out[-1]
            if lc(prev.text) == "zero" and not prev.locked:
                prev.text = "$0"
                prev.locked = True
                prev.trail = tok.trail or prev.trail
                prev.src = _merge_src(prev, tok)
                i += 1
                continue

        # times -> X after a number (no space)
        # (X locks the token — apply Rule-8 comma HERE: "two thousand times" -> "2,000X".)
        if w == "times" and out:
            prev = out[-1]
            pv = parse_int_token(prev.text)
            if pv is not None and not prev.locked:
                prev.text = f"{_num_for_suffix(pv, prev)}X"
                prev.locked = True
                prev.trail = tok.trail or prev.trail
                prev.src = _merge_src(prev, tok)
                i += 1
                continue

        # spoken/typed "x" multiplier normalization: 10x -> 10X, 10x'ing -> 10X'ing, 2xing->2Xing
        m = re.fullmatch(r"(\d+)x('?\w*)", w)
        if m and tok.text[0].isdigit():
            num = m.group(1)
            suffix = tok.text[len(num) + 1:]  # preserve original-case suffix after x
            tok.text = f"{num}X{suffix}"
            tok.locked = True
            out.append(tok)
            i += 1
            continue

        out.append(tok)
        i += 1
    return out


# ---------------------------------------------------------------------------
# Rule 7 (impl): Thousands separators
# ---------------------------------------------------------------------------

def apply_thousands(tokens):
    for tok in tokens:
        if tok.locked:
            continue
        # Rule 8: ONLY spoken-number values (produced by number-WORD expansion) get
        # thousands separators. A pre-existing digit literal ("5000", "12000", a year
        # "2024") is left untouched — it lacks spoken provenance.
        if not tok.spoken:
            continue
        # We only add commas to tokens that are pure digits (no $, no #).
        if re.fullmatch(r"\d+", tok.text):
            v = int(tok.text)
            if v >= 1000:
                tok.text = add_thousands(v)
    return tokens


# ---------------------------------------------------------------------------
# Rule 8 (impl): Capitalization
# ---------------------------------------------------------------------------

I_FORMS = {"i", "i'm", "i'll", "i'd", "i've"}


def apply_capitalization(tokens):
    for tok in tokens:
        if tok.locked:
            continue
        core = tok.text
        if core == "":
            continue
        w = lc(core)

        # "I" forms
        if w in I_FORMS:
            tok.text = _cap_i_form(w)
            continue
        # lowercase-locked abbreviations
        if w in LOWER_ABBREV:
            tok.text = w  # keep as-is lowercase
            continue
        # acronyms / proper words already correct stay (likely locked, but safety)
        if core in ACRONYMS or core in PROPER_WORDS:
            continue
        if core.upper() == core and len(core) > 1 and core.isalpha() and core in ACRONYMS:
            continue
        # case-insensitive proper-noun lookup: "elon" -> "Elon", "vegas" -> "Vegas", etc.
        canon = _PROPER_LOWER.get(w)
        if canon:
            tok.text = canon
            continue
        # possessive of a proper noun: "beast's"->"Beast's", "elon's"->"Elon's" (the bare lookup misses the 's)
        if (w.endswith("'s") or w.endswith("’s")) and _PROPER_LOWER.get(w[:-2]):
            tok.text = _PROPER_LOWER[w[:-2]] + "'s"
            continue
        # MULTI-PART proper tokens: Groq (and the corrections phrase-merge) emit a SINGLE token
        # spanning a multi-word name ("Roy Sutherland") or a hyphen compound ("IRL-based"). The
        # per-token lookups above can't match those, so we'd lowercase a real name. Split on
        # space/hyphen (keeping separators), recapitalize any subword that is a known proper noun /
        # acronym / "I"-form, and rejoin. Only rewrite if at least one subword hit (so ordinary
        # multi-word tokens fall through to default-lowercase and aren't spuriously Title-cased).
        if (" " in core) or ("-" in core):
            import re as _re
            _hit = False
            _parts = _re.split(r"([ \-])", core)
            for _i, _p in enumerate(_parts):
                if _p in (" ", "-") or _p == "":
                    continue
                # strip surrounding punctuation for the lookup ("Mr." -> "mr"), reattach after
                _m = _re.match(r"^(\W*)(.*?)(\W*)$", _p)
                _pre, _mid, _post = _m.group(1), _m.group(2), _m.group(3)
                _pw = lc(_mid)
                _pc = _PROPER_LOWER.get(_pw)
                if _pc:
                    _parts[_i] = _pre + _pc + _post; _hit = True
                elif _pw in I_FORMS:
                    _parts[_i] = _pre + _cap_i_form(_pw) + _post; _hit = True
                else:
                    _parts[_i] = _p.lower()
            if _hit:
                tok.text = "".join(_parts)
                continue
        # default lowercase
        tok.text = core.lower()
    return tokens


def _cap_i_form(w):
    if w == "i":
        return "I"
    # i'm -> I'm
    return "I" + w[1:]


# ---------------------------------------------------------------------------
# Rule 9 (impl): Punctuation cleanup
# ---------------------------------------------------------------------------

def apply_punctuation(tokens):
    for tok in tokens:
        # Clean lead/trail: drop commas and periods (with exceptions handled by being
        # inside number/abbrev/domain — those live INSIDE the core, not in trail).
        # Keep ? and ! in trail; keep apostrophes (they're inside cores).
        # Drop stray leading quotes/parens too (not in spec keep-list).
        new_trail = ""
        for ch in tok.trail:
            if ch in "?!":
                new_trail += ch
            # commas, periods dropped
        tok.trail = new_trail
        # leading punctuation: drop quotes/parens (not in keep-list)
        tok.lead = ""
        # Internal commas/periods are preserved (they're inside number/abbrev/domain
        # cores which were locked or are legitimate). We do NOT strip those.
    return tokens


# ---------------------------------------------------------------------------
# Reassembly
# ---------------------------------------------------------------------------

def reassemble(tokens):
    parts = []
    for idx, tok in enumerate(tokens):
        piece = tok.lead + tok.text + tok.trail
        if piece == "":
            continue
        if idx == 0:
            parts.append(piece)
        else:
            parts.append(" " + piece)
    return "".join(parts).strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def format_caption_text(text: str) -> str:
    if text is None:
        return ""
    if text.strip() == "":
        return ""
    tokens = tokenize(text)
    tokens = apply_misspellings(tokens)        # Step 0: STT error correction
    tokens = apply_dictionary(tokens)          # Rule 9 (dict) FIRST
    tokens = apply_spoken_decimals(tokens)     # Rule 3a: "two point five million" -> "2.5 million"
    tokens = apply_number_words(tokens)        # Rule 3
    tokens = apply_one_vs_1(tokens)            # Rule 4 (the "one" decision)
    tokens = apply_listicle_sequence(tokens)   # Rule 5 (listicle/sequence)
    tokens = apply_money(tokens)               # Rule 6 (money)
    tokens = apply_symbols_multipliers(tokens) # Rule 7 (symbols/multiplier)
    tokens = apply_thousands(tokens)           # Rule 8 (thousands)
    tokens = apply_capitalization(tokens)      # Rule 1 (capitalization)
    tokens = apply_punctuation(tokens)         # Rule 2 (punctuation cleanup)
    return reassemble(tokens)


def format_words(words):
    """Apply the reference editor's caption-text rules to a word-timestamp list, preserving timing.

    words: list of {"word","start","end", ...}. Returns a NEW list of
    {"word","start","end"} with the SAME rule sequence as format_caption_text applied,
    where merged tokens (number words, money phrases, %/X/$ folds, dictionary phrases)
    carry the start of their earliest source word and the end of their latest source word.
    Tokens that drop to empty text are removed. Never returns None timings, never crashes.
    """
    if not words:
        return []

    # Build one Token per input word, peeling lead/trail exactly like tokenize does, and
    # stamping src=[i] so provenance flows through every rule.
    tokens = []
    for i, w in enumerate(words):
        raw = (w.get("word") or "")
        core, lead, trail = _peel_token(raw)
        t = Token(core, lead=lead, trail=trail, space_before=(i != 0), src=[i])
        tokens.append(t)

    # SAME rule sequence as format_caption_text.
    tokens = apply_misspellings(tokens)        # Step 0: STT error correction
    tokens = apply_dictionary(tokens)
    tokens = apply_spoken_decimals(tokens)
    tokens = apply_number_words(tokens)
    tokens = apply_one_vs_1(tokens)
    tokens = apply_listicle_sequence(tokens)
    tokens = apply_money(tokens)
    tokens = apply_symbols_multipliers(tokens)
    tokens = apply_thousands(tokens)
    tokens = apply_capitalization(tokens)
    tokens = apply_punctuation(tokens)

    n_in = len(words)
    result = []
    prev_end = None
    for tok in tokens:
        text = tok.lead + tok.text + tok.trail
        if text == "":
            continue
        src = getattr(tok, "src", None) or []
        if src:
            lo = min(src)
            hi = max(src)
            start = words[lo].get("start")
            end = words[hi].get("end")
        else:
            # No provenance: fall back to nearest-neighbor timing so we never emit None.
            start = prev_end
            end = None
        if start is None:
            start = prev_end if prev_end is not None else (
                words[0].get("start", 0.0) if n_in else 0.0)
        if end is None:
            # carry the start of the next input word (or nudge past start).
            end = (words[min(hi + 1, n_in - 1)].get("start") if src and hi + 1 < n_in else None)
            if end is None or end <= start:
                end = start + 0.01
        result.append({"word": text, "start": start, "end": end})
        prev_end = end
    return result


def main():
    # --words mode: read a words-JSON, apply format_words, write a words-JSON.
    if len(sys.argv) > 1 and sys.argv[1] == "--words":
        import json
        inp_path = sys.argv[2]
        out_path = sys.argv[3]
        with open(inp_path) as f:
            d = json.load(f)
        words = d.get("words", d) if isinstance(d, dict) else d
        formatted = format_words(words)
        with open(out_path, "w") as f:
            json.dump({"words": formatted}, f, indent=2)
        return
    # default: stdin text mode.
    data = sys.stdin.read()
    sys.stdout.write(format_caption_text(data))


if __name__ == "__main__":
    main()
