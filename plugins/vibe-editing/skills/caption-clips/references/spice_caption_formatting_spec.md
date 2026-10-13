# Caption Transcript Formatter — the reference editor's company.com spec (authoritative)

> Source: the reference editor (company.com lead editor), provided 2026-06-11. This is the AUTHORITATIVE
> text-normalization standard for caption transcripts. Implemented in `scripts/spice_format.py`; runs as
> the normalize step of the spice caption chain. The worked examples below are the test suite
> (`scripts/test_spice_format.py`).

This skill formats raw caption transcript text to match company.com's caption style standards. Apply all rules below to the input text and return the formatted output.

## Processing Order
Apply transformations in this exact sequence to avoid conflicts between rules:

1. Common words dictionary — Match terms and lock in their final form. Dictionary output is authoritative and cannot be modified by any subsequent rule.
2. Number words → digits — Convert written numbers to numerals, including compound numbers
3. "one" vs "1" — Decide whether the word "one" stays a word or becomes the numeral 1
4. Listicle & sequence labels — Convert "number N" → "#N", and sequence words ("step one") → "step #1" / "day 1"
5. Money formatting — Apply dollar amount and scale formatting (requires step 2 first)
6. Symbol & multiplier rule — Convert "dollars"/"percent" → "$"/"%", and "times"/"x" → "X" (requires step 5 first)
7. Thousands separators — Add commas to spoken-number values ≥ 1,000
8. Capitalization — Apply lowercase default with all exceptions
9. Punctuation cleanup — Remove all disallowed punctuation

**Idempotency principle:** If a term is already in the correct output format (e.g., "$1K", "ROI", "1-on-1", "AI", "10X", "1,000"), do not attempt to re-process it. Leave it as is.

## Rule 1: Capitalization
All words are lowercased by default. Apply the following exceptions:
Always capitalize:
- "I" in all forms and contractions: I, I'm, I'll, I'd, I've
- All proper nouns and names: capitalize people, places, and brands
- All acronyms: AI, ROI, CEO, CFO, SOP, CAC, LTV, EBITDA, KPI, CTA, B2B, B2C
- Abbreviations and initialisms where capitalization is standard: U.S.
- "Billion" and "Trillion" when used as part of a money amount (see Rule 5)
- "X" as a multiplier suffix: 10X, 5X (see Rule 6)

Always keep lowercase (these are abbreviations but do not follow the capitalization rule): etc. · e.g. · i.e.

Sentence-starting words are never capitalized unless they qualify under one of the exceptions above.

## Rule 2: Punctuation
Remove:
- Commas — except when inside a number: 1,000
- Periods — except when inside a number ($1.2M), part of a standard abbreviation or initialism (U.S. / e.g. / i.e. / etc.), or part of a domain name (company.com)

Keep: Question marks `?` · Exclamation marks `!` · Apostrophes that are part of a contraction (I'm / I'll / I've / I'd / don't / can't)

## Rule 3: Number Words → Digits
Convert all number words to numerals. "one"→1 (but see Rule 4), two→2, three→3, ten→10, "twenty two"→22, fifty→50, "one hundred"→100, "five hundred"→500, "one thousand"→1,000 (apply to all number words and compound number words).

**Compound Numbers:** Combine adjacent number words built from multiplier words (hundred, thousand, million, billion, trillion) into a single numeral. "fifty thousand"→50,000 · "one hundred thousand"→100,000 · "five hundred"→500 · "two million"→2,000,000 · "three hundred million"→300,000,000

**The word "a" as a quantity:** "a" can stand in for "one". Outside money context, keep "a": "a thousand"→"a 1,000". Inside money context, "a" is consumed: "a thousand dollars"→"a $1,000" (article for the priced item), but a standalone money answer ("how much do you charge?" / "a thousand")→"$1,000". "a grand"→"$1,000".

**Exception — "zero":** "zero" stays "zero" UNLESS directly followed by: dollars, dollar, percent, percentage, %, $ — then "zero"→"0". "zero people"→"zero people"; "zero dollars"→"0 dollars"→$0; "zero percent"→"0 percent"→0%.

## Rule 4: "one" vs "1"
Applies only to "one". Default — convert "one"→"1" when used as a specific count (after most verbs, quantifiers, content words). "there's only one way"→"there's only 1 way"; "just one more rep"→"just 1 more rep"; "we closed one deal"→"we closed 1 deal"; "at least one person"→"at least 1 person"; "if there's one lesson"→"if there's 1 lesson".

Exception — keep "one" as a word when the IMMEDIATELY PRECEDING word is a conjunction, article, or discourse opener, AND the following word is not a money word or another number. Trigger words that keep "one": and, but, or, so, yet, nor, if, when, while, because, although, though, since, unless, after, before, until, as, once, whereas, whether, the, a, an, well, now, look, okay. "so one thing…"→kept; "one of the biggest mistakes"→kept (no preceding word); "and one day it clicked"→kept.

Dictionary-locked: "one shot" always kept as "one shot". Note: "one"→"1" if the next word is a money word (dollars, grand, etc.) or a number.

## Rule 5: Listicle & Sequence Labels
**Listicle ("number N"→"#N"):** Whenever "number" directly precedes a numeral, "number N"→"#N", regardless of preceding word. Runs after Rule 3. "number two"→"#2"; "number one"→"#1"; "law number 1"→"law #1"; "rule number 2"→"rule #2"; "step number 5"→"step #5"; "week number one"→"week #1".

**Sequence labels:** Use "#N" after these list-item words: step, tip, rule, law, point, reason, principle, habit, mistake, tactic, strategy, lesson, factor, secret, key. "step one"→"step #1"; "tip three"→"tip #3"; "reason one"→"reason #1". Use plain "N" (no hash) after these time/stage words: day, week, month, round, level, season. "day one"→"day 1"; "week one"→"week 1"; "round two"→"round 2". (If literally "week number one", the listicle rule wins → "week #1".)

## Rule 6: Money Formatting
| Spoken amount | Output | Example |
|---|---|---|
| zero dollars | $0 | "zero dollars"→$0 |
| $1–$999 | $N | "five hundred dollars"→$500 |
| $1,000–$19,999 | $N,000 or $NK (disambiguation) | see below |
| $20,000–$999,999 | $NK | "fifty thousand dollars"→$50K |
| $1M–$999.9M | $NM | "two million dollars"→$2M |
| $1B–$999.9B | $N Billion | "one billion dollars"→$1 Billion |
| $1T–$999.9T | $N Trillion | "one trillion dollars"→$1 Trillion |

Note: "Billion"/"Trillion" = full capitalized words with a space; "M"/"K" = single-letter, no space (intentional — B/T less universally recognized as abbreviations).

**$1,000–$19,999 disambiguation:** $N,000 when spoken as a full phrase ("a thousand dollars", "one thousand dollars", "five thousand dollars", "thousand dollars" → $1,000 / $5,000). $NK when spoken shorthand ("1k", "5k", "10k", "$1K" → $1K / $5K / $10K).

**"Grand" = $1,000:** multiply the number before it by $1,000, then apply the table. "a grand"/"one grand"→$1,000; "5 grand"/"five grand"→$5,000; "10 grand"/"ten grand"→$10,000; "100 grand"/"a hundred grand"→$100K; "500 grand"→$500K. $1,000–$19,999 grand expressions use $N,000; $20,000+ follow the table (K/M/Billion/Trillion).

**"$N" + scale word:** recombine a dollar-signed number + stranded scale word. "$100 million"→$100M; "$5 thousand"→$5,000; "$2 billion"→$2 Billion.

**Implied money context** (number formatted as money even without "dollars"/"$"), checked in order:
1. **Rate phrase (highest):** number directly followed by "a"/"an" + rate unit (hour, day, week, month, quarter, year) is money; the leading "a" meaning "one" is dropped. "a million a month"→"$1M a month"; "making a million a month"→"making $1M a month"; "two hundred thousand a week"→"$200K a week".
2. **Counting-noun guard (blocks money):** number directly followed by a counting noun is a quantity, NOT money. Counting nouns: year(s), month(s), week(s), day(s), hour(s), minute(s), point(s), people, person, customer(s), client(s), editor(s), employee(s), rep(s), user(s), member(s). "the past five years"→"the past 5 years"; "two points, three points"→"2 points, 3 points"; "five people"→"5 people".
3. **Very large numbers:** any number resolving to $100M or more is money, no trigger needed. "200 million or 500 million"→"$200M or $500M".
4. **Reliable trigger words:** number within ~8 words before / 3 after (incl. across a segment boundary) of: money, cash, cashflow, revenue, profit, EBITDA, pricing, price, cost, salary, fee, charge (charging/charged), budget, expense, income, earnings, wage, raise, rate → treat as money. "we did 44 million in revenue"→"we did $44M in revenue"; "how much do you charge?"/"a thousand"→"$1,000"; "cash was tight we only had five thousand left"→"$5,000 left".

"margin" is NOT a reliable money trigger (numbers near it are usually %). Context-neutral words (do NOT trigger alone): deal, sale, sales, payment, invoice, quote, bonus — only money-format near these if an explicit money indicator is present. "a 100 dollar deal"→$100 deal; "50 sales"→"50 sales"; "one deal"→"1 deal".

**Year guard:** a bare 4-digit number that looks like a year (1900–2099) spoken as DIGITS (not number words) is NOT money-formatted, even next to a trigger. "in 2024 our revenue grew"→stays "in 2024…". Word-spoken amounts ("two thousand") are exempt. When ambiguous, do NOT money-format; bare single/double-digit implicit-money ("we went from 5 to 44") are NOT auto-detected — leave for manual review.

## Rule 7: Symbol & Multiplier Rule
Convert trailing unit words to symbols when they directly follow a number. dollar/dollars→`$` before the number ("fifty dollars"→$50); percent/percentage→`%` after ("fifty percent"→50%); times→`X` after, no space ("five times"→5X). "thirty percent growth"→"30% growth"; "ten times bigger"→"10X bigger"; "100 times"→"100X". Normalize spoken/typed "x" multiplier to "X": "10x"→"10X"; "10x'ing"→"10X'ing"; "2xing"→"2Xing". Do NOT convert when the unit word has no specific number directly before it: "a lot of dollars"→"dollars"; "what percentage did you get"→"percentage"; "many times over"→"times". Runs after Rule 6; don't reprocess an already-formatted money amount.

## Rule 8: Thousands Separators
Any number spoken as words resolving to ≥1,000 is written with comma separators, even when not money. "one thousand"→1,000; "fifty thousand"→50,000; "two hundred thousand"→200,000. Spoken-number values ONLY — a pre-existing digit token (e.g. a year "2024" written as digits) is left untouched. "back in 2024 we launched"→no comma. Money amounts follow their own comma rules ($1,000–$19,999) and K/M/Billion/Trillion above.

## Rule 9: Common Words Dictionary
The dictionary is the source of truth — it overrides EVERY other rule. Once a term matches, its output is final.
Special phrases: "one on one"→1-on-1 · "one shot"→one shot (locks "one" as a word).
Names/brands: capitalize people, places, and brands (e.g. company.com).
Acronyms: AI · ROI · CAC · LTV · EBITDA · KPI/KPIs · CTA/CTAs · B2B · B2C · CEO · CFO.

## Known Gaps (pending)
- Spoken acronym variations ("B to B"→B2B, "return on investment"→ROI, "artificial intelligence"→AI) — pending the full variations list.
- Contextual millions on bare digits ("we went from 5 to 44" = $5M to $44M) — NOT auto-detected; manual review.
- Names and their common mishearings now live in `brand/caption-style/names.json` (proper / phrases / words / misspellings). Compound numbers ("twenty-five thousand"=25,000) and spoken decimals ("two point three million dollars"=$2.3M, "six point eight seven five percent"=6.875%) are handled; a spoken decimal only becomes "X.Y" when a scale word or "percent" follows.
- "etc." keeps its period (LOWER_ABBREV in spice_format.py); other periods are dropped per Rule 2.
