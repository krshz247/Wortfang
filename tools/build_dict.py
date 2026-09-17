#!/usr/bin/env python3
"""
Build Wortschatz's offline German→English dictionary from Wiktionary data
extracted by Wiktextract (https://kaikki.org).

    python3 tools/build_dict.py            # download + build into ./dict
    python3 tools/build_dict.py --help     # options

Output (in ./dict next to index.html):
    nouns.json   [[word, genders, plurals, senses, rank], ...]
    verbs.json   [[infinitive, senses, praesens, praeteritum, partizip, aux, flags, rank], ...]
    meta.json    counts, build date, sources, license
    ATTRIBUTION.txt

Only the Python 3 standard library is used. Downloads use the system `curl`.
Data license: Wiktionary content is CC BY-SA 4.0 (and GFDL); the generated
files must keep that license and attribution.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

GERMAN_URL = "https://kaikki.org/dictionary/German/kaikki.org-dictionary-German.jsonl"
RAW_URL = "https://kaikki.org/dictionary/raw-wiktextract-data.jsonl.gz"
FREQ_URLS = [
    "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/de/de_50k.txt",
    "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2016/de/de_50k.txt",
]
UNRANKED = 999_999
POS_RE = re.compile(r'"pos":\s?"([a-z_]+)"')
GITHUB_WEB_UPLOAD_LIMIT = 25 * 1024 * 1024

PERSONS = [
    ("first-person", "singular"),
    ("second-person", "singular"),
    ("third-person", "singular"),
    ("first-person", "plural"),
    ("second-person", "plural"),
    ("third-person", "plural"),
]
GENDER_CODE = {"masculine": "m", "feminine": "f", "neuter": "n"}
EXCLUDE_FORM_TAGS = {
    "subjunctive", "subjunctive-i", "subjunctive-ii", "imperative",
    "dependent", "subordinate-clause", "error-unrecognized-form",
}
SKIP_SENSE_TAGS = {"form-of", "alt-of", "misspelling", "alternative"}
LABEL_TAGS = [
    "reflexive", "colloquial", "informal", "slang", "vulgar", "derogatory", "offensive",
    "humorous", "formal", "literary", "poetic", "euphemistic", "figuratively",
    "dated", "archaic", "obsolete", "rare", "regional", "dialectal",
    "Austria", "Switzerland", "Southern-Germany", "Northern-Germany",
]
LABEL_TEXT = {"figuratively": "figurative", "Southern-Germany": "southern Germany",
              "Northern-Germany": "northern Germany"}
MAX_SENSES = 5
MAX_GLOSS = 140
# Frequency lists are lower-case, so the noun "Ich" (the ego) or "Hast" (haste) would inherit
# the rank of "ich"/"hast". Nouns whose lower-case form is also another German word
# (function word, adjective, or any verb form) get their rank damped.
FUNCTION_POS = {"pron", "det", "article", "conj", "prep", "adv", "particle", "intj", "num",
                "contraction", "postp", "prep_phrase", "adj"}
VERB_CONFLICT_POS = {"article", "num"}


# --------------------------------------------------------------------------- utils
def log(*a):
    print(*a, file=sys.stderr, flush=True)


def download(url: str, dest: Path) -> bool:
    """Download with curl (resumable). Returns True on success."""
    if dest.exists() and dest.stat().st_size > 0:
        log(f"✓ Using cached {dest.name} ({dest.stat().st_size / 1e6:.0f} MB)")
        return True
    if not shutil.which("curl"):
        log("✗ curl not found. Download manually and pass --input.")
        return False
    part = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"↓ Downloading {url}")
    cmd = ["curl", "-L", "--fail", "--retry", "3", "-C", "-", "-#", "-o", str(part), url]
    r = subprocess.run(cmd)
    if r.returncode != 0:
        log(f"✗ Download failed (curl exit {r.returncode})")
        return False
    part.rename(dest)
    return True


def open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "rt", encoding="utf-8")


def load_freq(path: Path | None) -> dict[str, int]:
    if not path or not path.exists():
        return {}
    ranks: dict[str, int] = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            w = line.split(" ", 1)[0].strip().lower()
            if w and w not in ranks:
                ranks[w] = i + 1
    log(f"✓ Frequency list: {len(ranks):,} words")
    return ranks


def uniq(seq):
    seen, out = set(), []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def tagset(obj) -> set[str]:
    return set(obj.get("tags") or [])


def head_expansion(e) -> str:
    ht = e.get("head_templates") or []
    return (ht[0].get("expansion") or "") if ht else ""


# --------------------------------------------------------------------------- senses
def extract_senses(e, verb: bool) -> list[str]:
    out = []
    for s in e.get("senses") or []:
        tags = tagset(s)
        if s.get("form_of") or s.get("alt_of") or tags & SKIP_SENSE_TAGS:
            continue
        glosses = s.get("glosses") or []
        if not glosses:
            continue
        g = re.sub(r"\s+", " ", glosses[-1]).strip()
        if not g or g.lower().startswith(("plural of", "inflection of", "alternative form", "obsolete form",
                                           "obsolete spelling", "misspelling of", "abbreviation of",
                                           "clipping of", "initialism of", "diminutive of", "see ",
                                           "former spelling", "archaic spelling", "dated spelling",
                                           "nonstandard spelling", "pre-reform", "superseded spelling")):
            continue
        if len(g) > MAX_GLOSS:
            g = g[: MAX_GLOSS - 1].rstrip(" ,;") + "…"
        labels = [LABEL_TEXT.get(t, t) for t in LABEL_TAGS if t in tags]
        if not verb:
            labels = [l for l in labels if l != "reflexive"]
        text = f"({', '.join(labels)}) {g}" if labels else g
        if text not in out:
            out.append(text)
        if len(out) >= MAX_SENSES:
            break
    return out


# --------------------------------------------------------------------------- nouns
def noun_genders(e, word: str) -> str:
    """Return gender codes in head order, e.g. 'm', 'mn', or 'p' (plural only)."""
    exp = head_expansion(e)
    if exp.startswith(word):
        head = exp[len(word):].split("(", 1)[0]
        toks = re.findall(r"\b(m|f|n|pl)\b", head)
        codes = uniq("p" if t == "pl" else t for t in toks)
        if codes:
            return "".join(codes)
    tags = tagset(e)
    if "plural-only" in tags or "plurale-tantum" in tags:
        return "p"
    g = "".join(c for t, c in GENDER_CODE.items() if t in tags)
    if not g:
        for s in e.get("senses") or []:
            st = tagset(s)
            g = "".join(c for t, c in GENDER_CODE.items() if t in st)
            if g:
                break
    return g


def noun_plurals(e, word: str, genders: str) -> str | None:
    """'Äpfel', 'Wasser|Wässer', '' (no plural) or None (unknown)."""
    if genders == "p":
        return word
    tags = tagset(e)
    forms = e.get("forms") or []
    head = [f.get("form", "").strip() for f in forms
            if tagset(f) == {"plural"} and f.get("source") not in ("declension",)]
    if not head:
        head = [f.get("form", "").strip() for f in forms
                if {"nominative", "plural"} <= tagset(f)]
    head = [p for p in uniq(head) if p not in ("-", "—") and " " not in p]
    if head:
        return "|".join(head[:3])
    if tags & {"no-plural", "uncountable", "singular-only"} or "no plural" in head_expansion(e):
        return ""
    return None


# --------------------------------------------------------------------------- verbs
def pick_form(forms, tense: str, person: str, number: str) -> str:
    want = {tense, person, number, "indicative"}
    cands = []
    for f in forms:
        t = tagset(f)
        if want <= t and not (t & EXCLUDE_FORM_TAGS):
            form = (f.get("form") or "").strip()
            if form and form != "-":
                cands.append(form)
    cands = uniq(cands)
    if not cands:
        return ""
    spaced = [c for c in cands if " " in c]
    return spaced[0] if spaced else cands[0]


def verb_aux(e) -> str:
    normal, rare = [], []
    for f in e.get("forms") or []:
        t = tagset(f)
        form = (f.get("form") or "").strip()
        if "auxiliary" in t and form in ("haben", "sein"):
            (rare if t & {"rare", "dated", "archaic", "obsolete", "regional"} else normal).append(form)
    if not normal and not rare:
        m = re.search(r"auxiliary ([a-z ()]+)", head_expansion(e))
        if m:
            txt = m.group(1)
            for a in ("haben", "sein"):
                if re.search(rf"\(rare\)\s*{a}", txt):
                    rare.append(a)
                elif a in txt:
                    normal.append(a)
    normal, rare = uniq(normal), [r for r in uniq(rare) if r not in normal]
    code = {"haben": "h", "sein": "s"}
    return ",".join([code[a] for a in normal] + [f"({code[a]})" for a in rare])


def standard_ich_for_ern(inf: str, ich: str, du: str) -> str:
    """wandern: prefer 'wandere' over the contracted 'wandre'."""
    if not inf.endswith("ern") or not ich or not du:
        return ich
    ich_first, _, ich_rest = ich.partition(" ")
    du_first = du.split(" ", 1)[0]
    if not du_first.endswith("st"):
        return ich
    stem = du_first[:-2]  # wander
    if len(stem) >= 3 and stem.endswith("er") and ich_first == stem[:-2] + "re":
        return (stem + "e") + ((" " + ich_rest) if ich_rest else "")
    return ich


def extract_verb(e):
    inf = (e.get("word") or "").strip()
    forms = e.get("forms") or []
    pr = [pick_form(forms, "present", p, n) for p, n in PERSONS]
    pt = [pick_form(forms, "preterite", p, n) for p, n in PERSONS]
    if not all(pt):
        pt = [pick_form(forms, "past", p, n) for p, n in PERSONS]
    part = ""
    for f in forms:
        t = tagset(f)
        if {"participle", "past"} <= t and not (t & {"error-unrecognized-form"}):
            part = (f.get("form") or "").strip()
            break
    aux = verb_aux(e)
    if not (all(pr) and all(pt) and part and aux):
        return None
    pr[0] = standard_ich_for_ern(inf, pr[0], pr[1])
    senses = extract_senses(e, verb=True)
    if not senses:
        return None
    flags = ""
    if any(" " in x for x in pr):
        flags += "s"
    if all(s.startswith("(reflexive") for s in senses):
        flags += "r"
    return [inf, senses, "|".join(pr), "|".join(pt), part, aux, flags]


# --------------------------------------------------------------------------- main build
def valid_headword(w: str) -> bool:
    return len(w) >= 2 and " " not in w and not w.startswith("-") and not w.endswith("-") \
        and not any(ch.isdigit() for ch in w) and len(w) <= 40


def build(input_path: Path, out_dir: Path, freq: dict[str, int], max_nouns: int, max_verbs: int,
          only_ranked: bool):
    nouns: dict[tuple, list] = {}
    verbs: dict[tuple, list] = {}
    function_words: set[str] = set()
    verb_conflicts: set[str] = set()
    stats = {"lines": 0, "german": 0, "noun_seen": 0, "verb_seen": 0,
             "noun_skipped": 0, "verb_skipped": 0}
    t0 = time.time()
    with open_text(input_path) as f:
        for line in f:
            stats["lines"] += 1
            if stats["lines"] % 200_000 == 0:
                log(f"  … {stats['lines']:,} lines, {len(nouns):,} nouns, {len(verbs):,} verbs "
                    f"({time.time() - t0:.0f}s)")
            # Cheap pre-filters before JSON parsing (matters for the all-languages dump).
            if '"German"' not in line and '"de"' not in line:
                continue
            m = POS_RE.search(line)
            lpos = m.group(1) if m else ""
            if lpos in FUNCTION_POS:
                try:
                    fe = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if fe.get("lang_code", "de") == "de" or fe.get("lang") == "German":
                    fw = (fe.get("word") or "").strip().lower()
                    function_words.add(fw)
                    if fe.get("pos") in VERB_CONFLICT_POS:
                        verb_conflicts.add(fw)
                continue
            if '"pos": "noun"' not in line and '"pos": "verb"' not in line \
                    and '"pos":"noun"' not in line and '"pos":"verb"' not in line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("lang_code", "de") != "de" and e.get("lang") != "German":
                continue
            stats["german"] += 1
            word = (e.get("word") or "").strip()
            pos = e.get("pos")
            if pos == "verb" and word:
                function_words.add(word.lower())  # includes inflected-form entries (hast, muss)
            if not valid_headword(word):
                continue
            rank = freq.get(word.lower(), UNRANKED)
            if only_ranked and rank == UNRANKED:
                continue

            if pos == "noun":
                stats["noun_seen"] += 1
                if not word[0].isupper():
                    stats["noun_skipped"] += 1
                    continue
                senses = extract_senses(e, verb=False)
                g = noun_genders(e, word)
                if not senses or not g:
                    stats["noun_skipped"] += 1
                    continue
                pl = noun_plurals(e, word, g)
                key = (word, g)
                if key in nouns:
                    row = nouns[key]
                    row[3] = uniq(row[3] + senses)[:MAX_SENSES]
                    if row[2] is None and pl is not None:
                        row[2] = pl
                else:
                    nouns[key] = [word, g, pl, senses, rank]

            elif pos == "verb":
                stats["verb_seen"] += 1
                v = extract_verb(e)
                if not v:
                    stats["verb_skipped"] += 1
                    continue
                key = (word, v[2], v[3])
                if key in verbs:
                    row = verbs[key]
                    row[1] = uniq(row[1] + v[1])[:MAX_SENSES]
                else:
                    verbs[key] = v + [rank]

    # Damp ranks inherited from homographs in other parts of speech (see FUNCTION_POS).
    damped = 0
    for r in nouns.values():
        if r[4] != UNRANKED and r[0].lower() in function_words:
            r[4] = r[4] * 10 + 1000
            damped += 1
    for r in verbs.values():
        if r[7] != UNRANKED and r[0].lower() in verb_conflicts:
            r[7] = r[7] * 10 + 1000
            damped += 1
    stats["damped"] = damped

    def fallback_rank(word, senses):
        # Without frequency data: shorter words with more senses first.
        return UNRANKED + len(word) * 10 - len(senses)

    noun_rows = sorted(nouns.values(), key=lambda r: (r[4] if r[4] != UNRANKED else fallback_rank(r[0], r[3]), r[0]))
    verb_rows = sorted(verbs.values(), key=lambda r: (r[7] if r[7] != UNRANKED else fallback_rank(r[0], r[1]), r[0]))
    if max_nouns:
        noun_rows = noun_rows[:max_nouns]
    if max_verbs:
        verb_rows = verb_rows[:max_verbs]
    for r in noun_rows:
        if r[2] is None:
            r[2] = "?"  # unknown plural

    out_dir.mkdir(parents=True, exist_ok=True)
    dump = lambda obj, name: (out_dir / name).write_text(
        json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    dump(noun_rows, "nouns.json")
    dump(verb_rows, "verbs.json")
    meta = {
        "version": 1,
        "built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
        "nouns": len(noun_rows),
        "verbs": len(verb_rows),
        "ranked": bool(freq),
        "source": "English Wiktionary (German entries), extracted by Wiktextract / kaikki.org",
        "license": "CC BY-SA 4.0",
        "formats": {
            "nouns": "[word, genders m|f|n|p (head order), plurals ('a|b', '' = none, '?' = unknown), senses[], rank]",
            "verbs": "[infinitive, senses[], praesens 'ich|du|er|wir|ihr|sie', praeteritum (same), partizip, "
                     "aux ('h','s','h,s','s,(h)' — parentheses = rare), flags ('s' separable, 'r' reflexive-only), rank]",
        },
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "ATTRIBUTION.txt").write_text(
        "Dictionary data in this folder is derived from Wiktionary (https://en.wiktionary.org),\n"
        "extracted with Wiktextract (Tatu Ylonen, https://github.com/tatuylonen/wiktextract)\n"
        "and published at https://kaikki.org. Licensed CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/).\n"
        "Changes: filtered to German nouns and verbs, reformatted, glosses shortened.\n"
        + ("Word-frequency ranks: FrequencyWords by Hermit Dave (OpenSubtitles), CC BY-SA 4.0.\n" if freq else ""),
        encoding="utf-8")

    log("")
    log(f"✓ Done in {time.time() - t0:.0f}s — scanned {stats['lines']:,} lines ({stats['german']:,} German noun/verb entries)")
    log(f"  nouns: {len(noun_rows):,} kept (skipped {stats['noun_skipped']:,})")
    log(f"  ranks damped for {stats['damped']:,} homographs of function words")
    log(f"  verbs: {len(verb_rows):,} kept (skipped {stats['verb_skipped']:,} incomplete)")
    for name in ("nouns.json", "verbs.json"):
        size = (out_dir / name).stat().st_size
        warn = "  ⚠ over GitHub's 25 MB web-upload limit — use --max-nouns/--max-verbs or upload with git" \
            if size > GITHUB_WEB_UPLOAD_LIMIT else ""
        log(f"  {name}: {size / 1e6:.1f} MB{warn}")
    return meta


def main():
    here = Path(__file__).resolve().parent
    app_dir = here.parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, help="Use an already-downloaded .jsonl or .jsonl.gz file")
    ap.add_argument("--source", choices=["german", "raw"], default="german",
                    help="german = kaikki German file (~1 GB, default); raw = full Wiktextract dump (~2.7 GB gz, all languages)")
    ap.add_argument("--cache", type=Path, default=app_dir / "build-cache", help="Where downloads are kept")
    ap.add_argument("--out", type=Path, default=app_dir / "dict", help="Output folder (default: ./dict)")
    ap.add_argument("--freq", type=Path, help="Frequency list file ('word count' per line); default: download")
    ap.add_argument("--no-freq", action="store_true", help="Skip the frequency list")
    ap.add_argument("--max-nouns", type=int, default=0, help="Keep only the N most frequent nouns (0 = all)")
    ap.add_argument("--max-verbs", type=int, default=0, help="Keep only the N most frequent verbs (0 = all)")
    ap.add_argument("--only-ranked", action="store_true", help="Keep only words found in the frequency list")
    args = ap.parse_args()

    freq_path = None
    if not args.no_freq:
        if args.freq:
            freq_path = args.freq
        else:
            for url in FREQ_URLS:
                p = args.cache / url.rsplit("/", 1)[-1]
                if download(url, p):
                    freq_path = p
                    break
            if not freq_path:
                log("! Continuing without a frequency list (search ranking will be weaker).")
    freq = load_freq(freq_path)
    if args.only_ranked and not freq:
        sys.exit("--only-ranked needs a frequency list")

    if args.input:
        src = args.input
    elif args.source == "german":
        src = args.cache / "kaikki.org-dictionary-German.jsonl"
        if not download(GERMAN_URL, src):
            log("! German file unavailable (it is marked deprecated on kaikki.org). Falling back to the full dump.")
            src = args.cache / "raw-wiktextract-data.jsonl.gz"
            if not download(RAW_URL, src):
                sys.exit("✗ Could not download the dictionary data.")
    else:
        src = args.cache / "raw-wiktextract-data.jsonl.gz"
        if not download(RAW_URL, src):
            sys.exit("✗ Could not download the dictionary data.")

    log(f"→ Building from {src}")
    build(src, args.out, freq, args.max_nouns, args.max_verbs, args.only_ranked)
    log(f"\nOutput written to {args.out}")
    log("You can delete the build-cache folder afterwards to free disk space.")


if __name__ == "__main__":
    main()
