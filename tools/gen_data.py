"""Generate synthetic, labelled SLP documents (SPEC 7).

    dev  data: Claude (claude -p, Sonnet 5), many styles; used for tuning only.
    gate data: GPT-6.1 Sol (codex exec, high reasoning), a different model
               family, plus a general-text stratum; never used for tuning.

Every identifier is written inline as [[TYPE|text]] and every clinical token
that must survive as [[KEEP|text]] (src/localdeid/evaldata.py), so labels are
exact. All content is fictional. Generators run with no MCP servers and no
tools, so they cannot read or write anything else.

    .venv/bin/python tools/gen_data.py dev  --n 96 --workers 6
    .venv/bin/python tools/gen_data.py gate --workers 6
    .venv/bin/python tools/gen_data.py check data/gate/gate.jsonl   # stats only
    .venv/bin/python tools/gen_data.py dev --name dev2 --prompt-version 2 --seed 11 --n 120
    .venv/bin/python tools/gen_data.py gate --name gate3 --prompt-version 3 --seed 41 \
        --notes 35 --long 10 --general 10
    .venv/bin/python tools/gen_data.py dev --name dev3 --prompt-version 3 --seed 29 --n 120 --long-heavy
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from localdeid.evaldata import Doc, MarkupError, dump_jsonl, load_jsonl, parse  # noqa: E402

LANGS = {
    "en": "English",
    "es": "Spanish (US Spanish, the clinician writes the whole document in Spanish)",
    "mixed": "English with Spanish mixed in: family quotes, Spanish section headings or phrases, "
             "code-switching within sentences, as a bilingual clinician would write",
}
DOC_TYPES = {
    "daily_note": ("a daily therapy/session note (SOAP or narrative)", (250, 600), 8),
    "evaluation": ("a speech-language evaluation or assessment report with header fields, background, "
                   "birth and medical history, family and language history, test results with a score "
                   "table, impressions, and recommendations", (1800, 3500), 30),
    "ifsp": ("an NC Infant-Toddler Program IFSP or IFSP review: family information, service coordinator, "
             "CDSA office, family concerns and priorities in the family's own words, daily routines, other "
             "agencies, outcomes with strategies, and service dates", (1200, 2500), 25),
    "general": ("a short general text: a letter or email to a parent, a message to a teacher, or a plain-"
                "prose summary for a school team", (150, 450), 5),
}
STYLES = [
    "EMR export: a header block of 'Label: value' lines, then narrative sections",
    "SOAP format with S:, O:, A:, P: sections and terse clinical shorthand",
    "narrative paragraphs with few headings",
    "bulleted lists under ALL-CAPS headings",
    "a form with several 'Label: value' fields on one line separated by two or more spaces",
    "tables pasted as tab-separated text (score tables, contact tables, service schedule)",
    "checkbox form lines such as '[X] Home  [ ] Daycare' mixed with free text",
    "abbreviation-heavy notes (pt, CG, mod cues, w/, b/c, approx)",
    "typed on an English keyboard: no accents on any Spanish word or name",
    "a long formal report with numbered sections and sub-headings",
    "a SALT-style header ('+ Name:', '+ CA:', '+ Context:') followed by a summary",
    "a parent-friendly letter style with greetings and plain language",
]
CHALLENGES = [
    "a child with a compound Spanish given name (e.g. 'María José') and two surnames",
    "a surname with a particle ('de la Cruz', 'del Río') and a hyphenated surname",
    "a sibling or relative whose name is also an ordinary word (Rosa, Sol, Luz, Hope, Will, Paz)",
    "a target word that equals a family member's name (e.g. target word 'sol', sister named Sol)",
    "nicknames or diminutives (Lupita, Chuy, Pepe, Teo, Juanito) alongside the full name",
    "initials for a person (e.g. 'M.R.' or 'J. Ortiz')",
    "honorific plus first name only ('doña Lupe', 'Miss Ana', 'la maestra Carmen', 'Mr. Tom')",
    "bystanders: classmates, cousins, a babysitter, and a pet with names",
    "birth and medical history with a named hospital, NICU, and physicians",
    "a parent's workplace and job ('Mom works nights at ...')",
    "agencies involved (CDSA office, DSS, WIC, Smart Start, Early Head Start) and a service coordinator",
    "Spanish-format dates ('3 de marzo de 2025', 'el martes 4 de junio') and numeric dates in several formats",
    "times of day and session start/stop times",
    "phone numbers in several formats, including a Mexican or Central American number",
    "an email address and a website URL",
    "a US street address and a Latin American address or hometown village",
    "Medicaid ID, insurance member ID, NC ITP child ID, and a record or authorization number",
    "CPT codes (92507, 92523), ICD-10 codes (F80.2), and accuracy data like 8/10 and 80%",
    "standardized tests whose names look like surnames (Rossetti, Goldman-Fristoe, Bayley, Peabody)",
    "an interpreter and other providers (OT, PT, pediatrician) by name",
    "the clinician's own name, credentials, and her employer (a fictional therapy company)",
    "a school or daycare with a teacher and classroom name ('Ms. Lopez's room')",
]
# Prompt v3 (after gate2, DECISIONS 25): more styles and challenges in the areas
# gate2 showed, and label rules that remove gold conflicts.
STYLES_V3 = [
    "speech-to-text dictation: no capitalization at sentence starts, few commas, run-on sentences",
    "an IFSP review table with one row per outcome (tab-separated: outcome, strategies, progress, date)",
    "an evaluation whose score table uses en-dash ranges (confidence intervals, age-equivalent ranges)",
    "a copied email thread with quoted replies ('>') and signature blocks",
]
CHALLENGES_V3 = [
    "dates inside a score table row next to scores and confidence-interval ranges",
    "a daycare and a therapy program whose names share head words ('Little Learners Daycare', "
    "'Little Learners Program')",
    "a pet and a sibling named inside the child's quoted productions",
    "a long Spanish agency name with its acronym in parentheses, later referred to by the acronym only",
    "a session schedule table with days, start and stop times, and locations",
    "therapy approaches named as proper nouns (Hanen, PROMPT, Cycles, PECS, Kaufman, It Takes Two to Talk)",
    "score ranges and age-equivalent ranges written with hyphens or en dashes ('73-83', '2;6–3;0')",
    "a parent who works two jobs, one named only by its acronym",
]
KEEP_HELP_V3 = """\
Label rules added in this version:
- If a name sits inside a quoted production or target phrase, mark the name with its identifier
  type and do NOT mark that phrase KEEP: he said "[[PET|Rocky]] run".
- Times of day are TIME, never KEEP.
- Score ranges, confidence intervals and age-equivalent ranges are KEEP including the hyphen or dash:
  [[KEEP|73-83]], [[KEEP|2;6–3;0]].
- Therapy approaches and program names are KEEP even when capitalized: [[KEEP|Hanen]], [[KEEP|PROMPT]].
"""
# Prompt v2 (after the phase 1 baseline): more organization forms, and every
# clinical token marked KEEP. The next dev and gate sets share this prompt.
ORG_CHALLENGES = [
    "the CDSA office and the service coordinator's agency named in a long Spanish form and a short form",
    "DSS, WIC, Smart Start, Early Head Start, or a Partnership for Children office named with its county",
    "the clinician's employer: a fictional therapy company named several ways (full legal name with PLLC, "
    "a short form, an abbreviation)",
    "parents' workplaces: a store, a factory or plant, a restaurant, a farm, a construction company, a "
    "cleaning service, written in English and in Spanish word order",
    "a daycare or preschool with an unusual name that does not contain the word daycare",
    "a hospital, NICU, pediatric clinic, ENT or audiology practice named with a health-system brand and campus",
    "a church, community center, library program, or nonprofit the family uses",
    "a school district and a specific school, plus a classroom or teacher name",
]
KEEP_HELP_V2 = """\
KEEP marking is exhaustive in this version. Mark EVERY one of these as [[KEEP|...]], each mention:
test and subtest names (PLS-5, CELF-5, Auditory Comprehension), standard scores, scaled scores,
percentiles, age equivalents, confidence intervals, ratios (8/10, 3 of 5), percentages, Apgar scores,
ages like 4;2 or 26 months, gestational age, birth weight, CPT and ICD-10 codes, modifiers, IPA and
phonemes (/k/, [w], /ɾ/), quoted child productions and target words, cueing levels (min/mod/max cues),
therapy approaches and programs (PECS, Hanen, PROMPT, Cycles), IFSP outcome or goal numbers.
"""
TYPES_HELP = """\
Identifier TYPES (mark every mention, each mention separately):
  CHILD   the client child (any name form: full, first, nickname, initials)
  PEER    other children: classmates, friends, group members
  PARENT  parents, guardians, stepparents
  SIBLING brothers and sisters
  FAMILY  other relatives, babysitters, nannies, caregivers
  TEACHER teachers and classroom staff
  PROVIDER physicians, other therapists, the clinician, interpreters, service coordinators
  PERSON  any other person
  PET     pets' names
  SCHOOL / DAYCARE / HOSPITAL / AGENCY / ORG / EMPLOYER   named organizations
          (ORG = a parent's workplace; EMPLOYER = the clinician's company; AGENCY = CDSA office, DSS, WIC,
          Smart Start, etc.)
  PLACE   street addresses, cities, towns, villages, counties, ZIP codes (NOT states or countries)
  DATE    dates (birth, service, evaluation, IFSP, review dates); NOT ages, NOT bare years
  TIME    times of day
  PHONE / EMAIL / URL
  ID / MEDICAID_ID   record, case, member, authorization, child ID numbers / Medicaid numbers
Clinical tokens to mark as KEEP (they must survive): standardized test names, scores, standard scores,
percentiles, ratios like 8/10 and percentages, ages like 4;2, CPT and ICD-10 codes, IPA and phonemes like
/k/ or [w], quoted child productions and target words, cueing levels (mod cues), SALT codes like [EW].
"""
RULES = """\
Markup rules:
- Write [[TYPE|exact text]] around the identifying characters only. Keep titles, kin words and
  possessives outside: "Ms. [[TEACHER|Lopez]]", "su mamá [[PARENT|Lucía Rodríguez]]",
  "[[CHILD|Mateo]]'s". A full name is one span: [[PARENT|Lucía Rodríguez]].
- Dates: one span for the whole date, year included: [[DATE|March 3, 2026]], [[DATE|03/14/2021]].
- Never nest markup. Never put "|" or a line break inside a span. Write a literal "[[" as "[[[[".
- Everything is fictional: invent all names, numbers, places, and organizations. Use realistic
  formats (NC phone numbers, 9-digit-plus-letter Medicaid IDs) but fake values.
- Output ONLY the document. No preamble, no explanation, no code fences.
"""


def jobs(kind: str, n: int, seed: int, prompt_version: int = 1, notes: int = 8, long: int = 5,
         general: int = 6, long_heavy: bool = False, dev_general: int = 0) -> list[dict]:
    rng = random.Random(seed)
    out = []
    if kind == "gate":
        plan = [(lang, dt) for lang in ("en", "es", "mixed") for dt, k in
                (("daily_note", notes), ("evaluation", long), ("ifsp", long)) for _ in range(k)]
        plan += [(lang, "general") for lang in ("en", "es") for _ in range(general)]
    else:
        types = ["daily_note", "evaluation", "ifsp"] if long_heavy else ["daily_note", "daily_note",
                                                                          "evaluation", "ifsp"]
        plan = [(rng.choice(list(LANGS)), rng.choice(types)) for _ in range(n)]
        plan += [(("en", "es")[i % 2], "general") for i in range(dev_general)]  # 0 keeps older sets identical
    styles = STYLES + STYLES_V3 if prompt_version >= 3 else STYLES
    org_pool = ORG_CHALLENGES + CHALLENGES_V3 if prompt_version >= 3 else ORG_CHALLENGES
    for i, (lang, dt) in enumerate(plan):
        lo, hi = DOC_TYPES[dt][1]
        k = 4 if dt in ("evaluation", "ifsp") else 3
        if prompt_version >= 2:
            challenges = rng.sample(org_pool, 2) + rng.sample(CHALLENGES, k - 1)
        else:
            challenges = rng.sample(CHALLENGES, k)
        out.append({
            "doc_id": f"{kind}-{i:04d}",
            "language": lang, "doc_type": dt,
            "style": rng.choice(styles),
            "challenges": challenges,
            "words": rng.randint(lo, hi),
            "prompt_version": prompt_version,
        })
    return out


def prompt(job: dict) -> str:
    desc = DOC_TYPES[job["doc_type"]][0]
    ch = "\n".join(f"  - {c}" for c in job["challenges"])
    return (
        "You write synthetic clinical documents for testing an offline de-identification tool. "
        "Everything must be fictional.\n\n"
        f"Write ONE document: {desc}, written by a bilingual pediatric speech-language pathologist "
        f"in North Carolina, in {LANGS[job['language']]}.\n"
        f"Layout and style: {job['style']}.\nLength: about {job['words']} words.\n"
        f"Include these on purpose:\n{ch}\n\n{TYPES_HELP}\n"
        + (KEEP_HELP_V2 + "\n" if job.get("prompt_version", 1) >= 2 else "")
        + (KEEP_HELP_V3 + "\n" if job.get("prompt_version", 1) >= 3 else "")
        + RULES
    )


def run_claude(text: str) -> str:
    cmd = ["env", "-u", "CLAUDE_CONFIG_DIR", "claude", "-p", "--model", "claude-sonnet-5",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--tools", "",
           "--no-session-persistence"]
    p = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=1800)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-500:])
    return p.stdout


def run_codex(text: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "last.txt"
        cmd = ["env", "-u", "CODEX_HOME", "-u", "CLAUDE_CONFIG_DIR", "codex", "exec", "-m", "gpt-6.1-sol",
               "-c", 'model_reasoning_effort="high"', "--ignore-user-config", "--ephemeral",
               "--skip-git-repo-check", "-s", "read-only", "-C", tmp, "-o", str(out), "-"]
        p = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=2400)
        if p.returncode != 0 or not out.exists():
            raise RuntimeError(p.stderr[-500:])
        return out.read_text(encoding="utf-8")


def clean(raw: str) -> str:
    t = raw.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip() + "\n"


def make(job: dict, kind: str, raw_dir: Path) -> Doc:
    gen = run_codex if kind == "gate" else run_claude
    last_err = None
    for attempt in range(3):
        raw = clean(gen(prompt(job)))
        (raw_dir / f"{job['doc_id']}.a{attempt}.txt").write_text(raw, encoding="utf-8")
        try:
            text, spans = parse(raw)
        except MarkupError as exc:
            last_err = f"markup: {exc}"
            continue
        n_ids = sum(1 for s in spans if s.type != "KEEP")
        if n_ids < DOC_TYPES[job["doc_type"]][2]:
            last_err = f"only {n_ids} identifiers"
            continue
        meta = {k: job[k] for k in ("language", "doc_type", "style", "challenges")}
        meta["prompt_version"] = job.get("prompt_version", 1)
        meta.update(generator="gpt-6.1-sol" if kind == "gate" else "claude-sonnet-5", attempt=attempt,
                    split=_split(job["style"]) if kind == "dev" else "gate")
        return Doc(job["doc_id"], text, spans, meta)
    raise RuntimeError(f"{job['doc_id']}: {last_err}")


def _split(style: str) -> str:
    """Dev split by template (style), so near-copies never cross splits (SPEC 4.8)."""
    return "dev_test" if int(hashlib.sha256(style.encode()).hexdigest(), 16) % 4 == 0 else "dev_train"


def cmd_generate(args: argparse.Namespace) -> None:
    name = args.name or args.kind
    out_dir = ROOT / "data" / name
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.jsonl"
    done = {d.doc_id: d for d in load_jsonl(out_path)} if out_path.exists() else {}
    planned = jobs(args.kind, args.n, args.seed, args.prompt_version, args.notes, args.long, args.general,
                   args.long_heavy, args.dev_general)
    for j in planned:
        j["doc_id"] = j["doc_id"].replace(f"{args.kind}-", f"{name}-")
    todo = [j for j in planned if j["doc_id"] not in done]
    print(f"{len(done)} done, {len(todo)} to generate", flush=True)
    lock = threading.Lock()
    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(make, j, args.kind, raw_dir): j for j in todo}
        for f in as_completed(futs):
            j = futs[f]
            try:
                d = f.result()
            except Exception as exc:  # noqa: BLE001 (report and continue)
                failures.append((j["doc_id"], str(exc)[:200]))
                print(f"FAIL {j['doc_id']}: {str(exc)[:200]}", flush=True)
                continue
            with lock:
                done[d.doc_id] = d
                dump_jsonl([done[k] for k in sorted(done)], out_path)
            print(f"ok   {d.doc_id} {d.meta['language']}/{d.meta['doc_type']} "
                  f"{len(d.text.split())}w {len(d.identifiers)} ids", flush=True)
    print(f"done: {len(done)} docs, {len(failures)} failures")
    cmd_check(argparse.Namespace(path=str(out_path)))


def cmd_check(args: argparse.Namespace) -> None:
    from collections import Counter

    docs = load_jsonl(args.path)
    by_stratum = Counter(f"{d.meta['language']}/{d.meta['doc_type']}" for d in docs)
    ids = Counter()
    per_lang = Counter()
    for d in docs:
        for s in d.identifiers:
            ids[s.type] += 1
            per_lang[d.meta["language"]] += 1
    print(f"{len(docs)} docs; strata: {dict(sorted(by_stratum.items()))}")
    print(f"identifiers per language: {dict(per_lang)}")
    print(f"identifiers per type: {dict(sorted(ids.items()))}")
    print(f"KEEP spans: {sum(len(d.keeps) for d in docs)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for kind in ("dev", "gate"):
        g = sub.add_parser(kind)
        g.add_argument("--n", type=int, default=96)
        g.add_argument("--workers", type=int, default=6)
        g.add_argument("--seed", type=int, default=20261010 if kind == "dev" else 7)
        g.add_argument("--name", default=None, help="output set name (data/<name>/<name>.jsonl)")
        g.add_argument("--prompt-version", type=int, default=1)
        g.add_argument("--notes", type=int, default=8, help="gate: daily notes per language")
        g.add_argument("--long", type=int, default=5, help="gate: evaluations and IFSPs per language")
        g.add_argument("--general", type=int, default=6, help="gate: general texts per language (en, es)")
        g.add_argument("--long-heavy", action="store_true", help="dev: equal thirds note/evaluation/IFSP")
        g.add_argument("--dev-general", type=int, default=0, help="dev: general texts added after --n (en, es)")
        g.set_defaults(func=cmd_generate, kind=kind)
    c = sub.add_parser("check")
    c.add_argument("path")
    c.set_defaults(func=cmd_check)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
