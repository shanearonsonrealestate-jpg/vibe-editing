#!/usr/bin/env python3
"""
test_spice_format.py — every worked input->output example from the spec.

Source: caption-clips/references/spice_caption_formatting_spec.md
Each CASES entry is (input, expected, why/rule). Run directly:
    python3 test_spice_format.py
Exit code 0 = all pass; nonzero = some fail (failures printed).
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spice_format import format_caption_text, format_words  # noqa: E402


# (input, expected, label)
CASES = [
    # ---------------- Rule 3: number words -> digits ----------------
    ("two", "2", "R3 two->2"),
    ("three", "3", "R3 three->3"),
    ("ten", "10", "R3 ten->10"),
    ("twenty two", "22", "R3 twenty two->22"),
    ("fifty", "50", "R3 fifty->50"),
    ("one hundred", "100", "R3 one hundred->100"),
    ("five hundred", "500", "R3 five hundred->500"),
    ("one thousand", "1,000", "R3/R8 one thousand->1,000"),
    # compound numbers
    ("fifty thousand", "50,000", "R3 fifty thousand->50,000"),
    ("one hundred thousand", "100,000", "R3 one hundred thousand->100,000"),
    ("two million", "2,000,000", "R3 two million->2,000,000"),
    ("three hundred million", "300,000,000", "R3 three hundred million->300,000,000"),

    # "a" as quantity (non-money keeps "a")
    ("a thousand", "a 1,000", "R3 a thousand (non-money) -> a 1,000"),

    # zero exception (non-money keeps word)
    ("zero people", "zero people", "R3 zero people stays"),

    # ---------------- Rule 4: one vs 1 ----------------
    ("there's only one way", "there's only 1 way", "R4 specific count -> 1"),
    ("just one more rep", "just 1 more rep", "R4 specific count -> 1"),
    ("we closed one deal", "we closed 1 deal", "R4 -> 1"),
    ("at least one person", "at least 1 person", "R4 -> 1"),
    ("if there's one lesson", "if there's 1 lesson", "R4 -> 1 (if...there's)"),
    # keep "one"
    ("so one thing", "so one thing", "R4 keep one after 'so'"),
    ("one of the biggest mistakes", "one of the biggest mistakes", "R4 keep one (no preceding word)"),
    ("and one day it clicked", "and one day it clicked", "R4 keep one after 'and'"),
    # dictionary-locked one shot
    ("one shot", "one shot", "R9 one shot locked"),
    # one -> 1 if next is money word or number
    ("one deal", "1 deal", "R5 neutral-word: one deal -> 1 deal"),

    # ---------------- Rule 5: listicle & sequence ----------------
    ("number two", "#2", "R5 listicle number two->#2"),
    ("number one", "#1", "R5 listicle number one->#1"),
    ("law number 1", "law #1", "R5 listicle law number 1->law #1"),
    ("rule number 2", "rule #2", "R5 listicle rule number 2->rule #2"),
    ("step number 5", "step #5", "R5 listicle step number 5->step #5"),
    ("week number one", "week #1", "R5 listicle wins: week number one->week #1"),
    # sequence hash words
    ("step one", "step #1", "R5 seq step one->step #1"),
    ("tip three", "tip #3", "R5 seq tip three->tip #3"),
    ("reason one", "reason #1", "R5 seq reason one->reason #1"),
    # sequence plain words
    ("day one", "day 1", "R5 seq day one->day 1"),
    ("week one", "week 1", "R5 seq week one->week 1"),
    ("round two", "round 2", "R5 seq round two->round 2"),

    # ---------------- Rule 6: money formatting ----------------
    ("zero dollars", "$0", "R6 zero dollars->$0"),
    ("five hundred dollars", "$500", "R6 five hundred dollars->$500"),
    ("fifty thousand dollars", "$50K", "R6 fifty thousand dollars->$50K"),
    ("two million dollars", "$2M", "R6 two million dollars->$2M"),
    ("one billion dollars", "$1 Billion", "R6 one billion dollars->$1 Billion"),
    ("one trillion dollars", "$1 Trillion", "R6 one trillion dollars->$1 Trillion"),
    # $1,000-$19,999 disambiguation
    ("a thousand dollars", "a $1,000", "R6 a thousand dollars (article kept)->a $1,000"),
    ("one thousand dollars", "$1,000", "R6 one thousand dollars->$1,000"),
    ("five thousand dollars", "$5,000", "R6 five thousand dollars->$5,000"),
    ("thousand dollars", "$1,000", "R6 thousand dollars->$1,000"),
    # standalone money answer in money context (the question supplies the context)
    ("how much do you charge a thousand", "how much do you charge $1,000",
     "R6 standalone money answer w/ 'charge' trigger -> $1,000"),

    # shorthand K
    ("1k", "1k", "R6 idempotency-ish: bare 1k with no dollars stays (no money ctx)"),
    ("$1K", "$1K", "idempotency $1K"),

    # grand
    ("a grand", "$1,000", "R6 a grand->$1,000"),
    ("one grand", "$1,000", "R6 one grand->$1,000"),
    ("five grand", "$5,000", "R6 five grand->$5,000"),
    ("ten grand", "$10,000", "R6 ten grand->$10,000"),
    ("a hundred grand", "$100K", "R6 a hundred grand->$100K"),
    ("500 grand", "$500K", "R6 500 grand->$500K"),
    ("100 grand", "$100K", "R6 100 grand->$100K"),
    ("200 grand", "$200K", "R6 200 grand->$200K"),
    ("100 grand or 200 grand", "$100K or $200K", "R6 two digit-grands in a line"),
    ("1.5 grand", "$1.5K", "R6 decimal grand->$1.5K"),

    # spoken-money SHORTHAND units (k / mil / m) after a clean numeric quantity
    ("50 k", "$50K", "R6 shorthand 50 k->$50K"),
    ("3 mil", "$3M", "R6 shorthand 3 mil->$3M"),
    ("10 m", "$10M", "R6 shorthand 10 m->$10M"),
    ("5 mil in revenue", "$5M in revenue", "R6 shorthand mil in context"),
    # word-number fraction money (money-gated: only before slang units)
    ("three and a half mil in revenue", "$3.5M in revenue", "R6 word-fraction mil->$3.5M"),
    ("five and a quarter mil", "$5.25M", "R6 word-fraction quarter->$5.25M"),
    # shorthand-unit guards: bare unit with no numeric quantity stays untouched
    ("vitamin k", "vitamin k", "R6 guard: 'k' not after a number stays"),
    ("plan m", "plan m", "R6 guard: 'm' not after a number stays"),
    ("three and a half years", "3 and a half years", "R6 guard: non-money fraction untouched"),

    # $N + scale recombination
    ("$100 million", "$100M", "R6 $100 million->$100M"),
    ("$5 thousand", "$5,000", "R6 $5 thousand->$5,000"),
    ("$2 billion", "$2 Billion", "R6 $2 billion->$2 Billion"),

    # implied money - rate phrase
    ("a million a month", "$1M a month", "R6 rate: a million a month->$1M a month"),
    ("making a million a month", "making $1M a month", "R6 rate: making $1M a month"),
    ("two hundred thousand a week", "$200K a week", "R6 rate: ->$200K a week"),

    # counting-noun guard
    ("the past five years", "the past 5 years", "R6 guard: 5 years"),
    ("two points three points", "2 points 3 points", "R6 guard: points"),
    ("five people", "5 people", "R6 guard: 5 people"),

    # very large numbers
    ("200 million or 500 million", "$200M or $500M", "R6 very-large implied money"),

    # reliable trigger words
    ("we did 44 million in revenue", "we did $44M in revenue", "R6 trigger revenue"),
    ("cash was tight we only had five thousand left", "cash was tight we only had $5,000 left", "R6 trigger cash"),

    # neutral words don't trigger
    ("a 100 dollar deal", "a $100 deal", "R6 explicit $ near 'deal'"),
    ("50 sales", "50 sales", "R6 neutral 'sales' no trigger"),

    # year guard
    ("in 2024 our revenue grew", "in 2024 our revenue grew", "R6 year guard 2024"),
    ("back in 2024 we launched", "back in 2024 we launched", "R8 year no comma"),

    # ---------------- Rule 7: symbol & multiplier ----------------
    ("fifty dollars", "$50", "R7 fifty dollars->$50"),
    ("fifty percent", "50%", "R7 fifty percent->50%"),
    ("five times", "5X", "R7 five times->5X"),
    ("thirty percent growth", "30% growth", "R7 30% growth"),
    ("ten times bigger", "10X bigger", "R7 10X bigger"),
    ("100 times", "100X", "R7 100 times->100X"),
    ("10x", "10X", "R7 10x->10X"),
    ("10x'ing", "10X'ing", "R7 10x'ing->10X'ing"),
    ("2xing", "2Xing", "R7 2xing->2Xing"),
    # do-not-convert (no specific number before unit)
    ("a lot of dollars", "a lot of dollars", "R7 no number before dollars"),
    ("what percentage did you get", "what percentage did you get", "R7 no number before percentage"),
    ("many times over", "many times over", "R7 no number before times"),
    ("zero percent", "0%", "R7/R3 zero percent->0%"),

    # ---------------- Rule 8: thousands separators ----------------
    ("two hundred thousand", "200,000", "R8 two hundred thousand->200,000"),

    # ---------------- Rule 9: dictionary ----------------
    ("one on one", "1-on-1", "R9 one on one->1-on-1"),
    ("company.com", "company.com", "R9 domain"),
    ("ai", "AI", "R9 acronym AI"),
    ("roi", "ROI", "R9 acronym ROI"),
    ("cac", "CAC", "R9 acronym CAC"),
    ("ltv", "LTV", "R9 acronym LTV"),
    ("ebitda", "EBITDA", "R9 acronym EBITDA"),
    ("kpi", "KPI", "R9 acronym KPI"),
    ("kpis", "KPIs", "R9 acronym KPIs"),
    ("cta", "CTA", "R9 acronym CTA"),
    ("ctas", "CTAs", "R9 acronym CTAs"),
    ("b2b", "B2B", "R9 acronym B2B"),
    ("b2c", "B2C", "R9 acronym B2C"),
    ("ceo", "CEO", "R9 acronym CEO"),
    ("cfo", "CFO", "R9 acronym CFO"),

    # ---------------- Rule 1: capitalization ----------------
    ("i", "I", "R1 I"),
    ("i'm", "I'm", "R1 I'm"),
    ("i'll", "I'll", "R1 I'll"),
    ("i'd", "I'd", "R1 I'd"),
    ("i've", "I've", "R1 I've"),
    ("etc.", "etc.", "R1 etc. lowercase"),
    ("e.g.", "e.g.", "R1 e.g. lowercase"),
    ("i.e.", "i.e.", "R1 i.e. lowercase"),
    # sentence start not capitalized
    ("the best thing i did", "the best thing I did", "R1 sentence start lowercase, I capped"),

    # ---------------- Rule 2: punctuation ----------------
    ("hello, world", "hello world", "R2 remove comma"),
    ("this is great.", "this is great", "R2 remove trailing period"),
    ("are you sure?", "are you sure?", "R2 keep ?"),
    ("that's amazing!", "that's amazing!", "R2 keep !"),
    ("don't do it", "don't do it", "R2 keep contraction apostrophe"),
    ("1,000", "1,000", "R2 keep comma inside number"),
    ("$1.2M", "$1.2M", "R2 keep period inside money"),
    ("U.S.", "U.S.", "R2 keep abbreviation periods"),

    # ---------------- Idempotency ----------------
    ("$1K", "$1K", "idem $1K"),
    ("ROI", "ROI", "idem ROI"),
    ("1-on-1", "1-on-1", "idem 1-on-1"),
    ("AI", "AI", "idem AI"),
    ("10X", "10X", "idem 10X"),
    ("1,000", "1,000", "idem 1,000"),

    # ---------------- Regression: adversarial-finding fixes (2026-06-11) ----------------
    # R1: spec-listed acronyms missing from the dict (capitalize from lowercase input)
    ("sop", "SOP", "R1 SOP acronym capitalized"),
    ("we wrote an sop", "we wrote an SOP", "R1 SOP mid-sentence"),
    ("u.s.", "U.S.", "R1 lowercase u.s. -> U.S."),
    ("the u.s. economy", "the U.S. economy", "R1 lowercase u.s. mid-sentence"),
    # R2: trailing comma after protected abbreviation/domain must be removed
    ("e.g.,", "e.g.", "R2 drop comma after e.g."),
    ("i.e.,", "i.e.", "R2 drop comma after i.e."),
    ("etc.,", "etc.", "R2 drop comma after etc."),
    ("U.S.,", "U.S.", "R2 drop comma after U.S. (keep cap)"),
    ("in the U.S., we win", "in the U.S. we win", "R2 drop comma after U.S. mid-sentence"),
    ("company.com,", "company.com", "R2 drop comma after domain (+cap)"),
    ("company.com,", "company.com", "R2 drop comma, keep correct domain"),
    # R8 lock-before-thousands: % / X suffix must not eat the thousands comma
    ("one thousand times", "1,000X", "R8+R7 1,000X comma survives multiplier"),
    ("a million times", "a 1,000,000X", "R8+R7 a 1,000,000X comma survives"),
    ("five thousand percent", "5,000%", "R8+R7 5,000% comma survives percent"),
    ("fifty thousand percent", "50,000%", "R8+R7 50,000% comma survives percent"),
    ("two thousand times", "2,000X", "R8+R7 2,000X comma survives"),
    # R3 zero exception via bare symbols
    ("zero %", "0%", "R3 zero + bare % -> 0%"),
    ("zero $", "$0", "R3 zero + bare $ -> $0"),
    # R8 spoken-value provenance: spoken 'two thousand'->2,000 (exempt from year guard)
    ("two thousand", "2,000", "R8 spoken two thousand -> 2,000 (not year-guarded)"),
    ("two thousand in revenue", "$2,000 in revenue", "R5 revenue trigger on spoken 2,000"),
    # R8 provenance: pre-existing digit literals are left untouched (no comma)
    ("5000", "5000", "R8 pre-existing digit literal untouched"),
    ("12000 steps", "12000 steps", "R8 pre-existing digit literal untouched (non-counting noun)"),
    # idempotency: thousands-comma multiplier preserved + X stays uppercase
    ("2,000X", "2,000X", "idem 2,000X (comma + uppercase X)"),
    # R6 shorthand-K in implied money context -> $NK
    ("the price is 5k", "the price is $5K", "R6 5k near 'price' -> $5K"),
    ("charging 10k", "charging $10K", "R6 10k near 'charging' -> $10K"),
    ("the price is 50k", "the price is $50K", "R6 50k near 'price' -> $50K"),
    # guard: shorthand-K with NO money context stays raw (not falsely money-formatted)
    ("i run 5k every morning", "I run 5k every morning", "R6 5k (no money ctx) stays 5k"),

    # ---------------- Regression: money amount + scale word (2026-06-16) ----------------
    # A "$N"/"$N.N" amount immediately before a scale word folds into ONE money token; the
    # scale word must NOT expand on its own. Bug: "$400 million" rendered "$400 1,000,000"
    # because the guards only matched a bare r"\$\d+" — missing the decimal form ("$1.5")
    # and the STT-glued-article form ("a $400", exercised in WORD_CASES below; text mode
    # splits on the space so the article never glues here). Fix: _money_scale_base.
    ("$5 million", "$5M", "money+scale $5 million->$5M"),
    ("$400 million", "$400M", "money+scale $400 million->$400M"),
    ("$1.5 billion", "$1.5 Billion", "money+scale DECIMAL ->$1.5 Billion (was '$1.5 $1 Billion')"),
    ("$1.5 million", "$1.5M", "money+scale DECIMAL ->$1.5M"),
    ("100 million dollars", "$100M", "money 100 million dollars->$100M"),
    ("five hundred million", "500,000,000", "no-$ five hundred million STAYS 500,000,000"),
    ("I made a $400 million exit", "I made a $400M exit", "money+scale in sentence, article 'a' kept"),
    ("it's a $2 billion company", "it's a $2 Billion company", "money+scale billion in sentence"),
    # NB the no-$ baselines "two million"->2,000,000 and the rate phrase
    # "a million a month"->$1M a month are already asserted above (R3 / R6 rate) and are
    # unchanged by this fix; the fix only ever fires when a "$" token precedes the scale word.
    # hyphenated compounds + spoken decimals (Whisper writes "twenty-five" as one token)
    ("twenty-five thousand", "25,000", "hyphenated compound + scale"),
    ("twenty-five thousand dollars", "$25K", "hyphenated compound money"),
    ("three hundred twenty-five thousand dollars", "$325K", "hundreds + hyphenated compound money"),
    ("two point three million dollars", "$2.3M", "spoken decimal money"),
    ("1.5 million dollars", "$1.5M", "digit decimal money"),
    ("2.5 percent", "2.5%", "decimal percent"),
    ("six point eight seven five percent", "6.875%", "spoken decimal percent"),
]

# Names from brand/caption-style/names.json — only run when that file is present.
BRAND_CASES = [
    ("I work with shane aronson and evan sophir at serhant in boca raton",
     "I work with Shane Aronson and Evan Sophir at SERHANT. in Boca Raton", "brand names cased"),
    ("the bogart florida team at sarhant sold a condo near misner park",
     "the Bogard Florida Team at SERHANT. sold a condo near Mizner Park", "brand mishearings fixed"),
    ("shane aaronson here from Serhant.", "Shane Aronson here from SERHANT.", "brand name at sentence end"),
]


def _words(*surfaces):
    """Build a minimal word-timestamp list (1s slots) from surface strings, the shape
    format_words consumes from the transcriber."""
    return [{"word": s, "start": float(i), "end": i + 0.5} for i, s in enumerate(surfaces)]


def _join_words(word_list):
    """Render a format_words() result back to a space-joined caption string."""
    return " ".join(t["word"] for t in word_list)


# (input word surfaces, expected joined output, label) for the WORD-TIMESTAMP path
# (format_words) — the real caption path, and the one the STT-glued-article bug lives on:
# Whisper emits the article fused to the amount as a SINGLE token ("a $400"), so the bug
# does NOT reproduce in text mode (which splits on the space). The scale word must fold,
# never expand to digits ("a $400" + "million" must be "a $400M", NOT "a $400 1,000,000").
WORD_CASES = [
    # the reported bug — glued article on the money token
    (["a $400", "million"], "a $400M", "WORD glued 'a $400' million -> a $400M (NOT 1,000,000)"),
    (["the $5", "million"], "the $5M", "WORD glued 'the $5' million -> the $5M"),
    (["an $8", "billion"], "an $8 Billion", "WORD glued 'an $8' billion -> an $8 Billion"),
    (["a $400", "million", "exit"], "a $400M exit", "WORD glued article mid-phrase"),
    (["I", "made", "a $400", "million", "exit"], "I made a $400M exit", "WORD glued in a sentence"),
    # un-glued money token + scale word (must match text mode)
    (["$400", "million"], "$400M", "WORD $400 million -> $400M"),
    (["$2", "billion"], "$2 Billion", "WORD $2 billion -> $2 Billion"),
    (["$1.5", "billion"], "$1.5 Billion", "WORD decimal $1.5 billion -> $1.5 Billion"),
    (["$5", "thousand"], "$5,000", "WORD $5 thousand -> $5,000"),
    # NO "$" anywhere -> number words still expand plainly in word mode (must NOT money-ize)
    (["two", "million"], "2,000,000", "WORD no-$ two million STAYS 2,000,000"),
    (["five", "hundred", "million"], "500,000,000", "WORD no-$ five hundred million STAYS"),
]


def run_word_cases():
    """Exercise format_words: correctness, idempotency (re-feeding the output is stable),
    and timing-metadata sanity (no None timings, end>start, starts non-decreasing)."""
    passed = 0
    failures = []
    for surfaces, expected, label in WORD_CASES:
        words = _words(*surfaces)
        out = format_words(words)
        actual = _join_words(out)
        # idempotency: feeding format_words its own output must not change the text
        twice = _join_words(format_words(out))
        # timing sanity
        timing_ok = all(
            (t.get("start") is not None and t.get("end") is not None
             and t["end"] > t["start"]) for t in out
        )
        starts = [t["start"] for t in out]
        monotonic = all(b >= a for a, b in zip(starts, starts[1:]))
        ok = (actual == expected) and (twice == actual) and timing_ok and monotonic
        if ok:
            passed += 1
        else:
            why = []
            if actual != expected:
                why.append("output")
            if twice != actual:
                why.append(f"idempotency (twice={twice!r})")
            if not timing_ok:
                why.append("timing")
            if not monotonic:
                why.append("start-order")
            failures.append((surfaces, expected, actual, label, ", ".join(why)))
    total = len(WORD_CASES)
    print(f"WORD-MODE PASSED {passed}/{total}")
    if failures:
        print(f"\nWORD-MODE FAILURES ({len(failures)}):")
        for surfaces, exp, act, label, why in failures:
            print(f"  [{label}]  (failed: {why})")
            print(f"    input:    {surfaces!r}")
            print(f"    expected: {exp!r}")
            print(f"    actual:   {act!r}")
    return passed, total, failures


def run():
    passed = 0
    failures = []
    for inp, expected, label in CASES:
        actual = format_caption_text(inp)
        if actual == expected:
            passed += 1
        else:
            failures.append((inp, expected, actual, label))
    total = len(CASES)
    print(f"PASSED {passed}/{total}")
    if failures:
        print(f"\nFAILURES ({len(failures)}):")
        for inp, exp, act, label in failures:
            print(f"  [{label}]")
            print(f"    input:    {inp!r}")
            print(f"    expected: {exp!r}")
            print(f"    actual:   {act!r}")
    return passed, total, failures


def run_brand_cases():
    from spice_format import _brand_names_path
    if _brand_names_path() is None:
        print("BRAND skipped (no brand/caption-style/names.json)")
        return 0, 0, []
    fails = [(i, e, format_caption_text(i), l) for i, e, l in BRAND_CASES if format_caption_text(i) != e]
    print(f"BRAND PASSED {len(BRAND_CASES) - len(fails)}/{len(BRAND_CASES)}")
    for inp, exp, act, label in fails:
        print(f"  [{label}]\n    input:    {inp!r}\n    expected: {exp!r}\n    actual:   {act!r}")
    return len(BRAND_CASES) - len(fails), len(BRAND_CASES), fails


if __name__ == "__main__":
    p, t, f = run()
    bp, bt, bf = run_brand_cases()
    wp, wt, wf = run_word_cases()
    sys.exit(0 if not f and not wf and not bf else 1)
