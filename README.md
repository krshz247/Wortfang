# Wortschatz

A personal German vocabulary tracker for nouns and verbs. It's a web app you add to your iPhone Home Screen.

- **Lookups work offline and need no API key.** They use a German→English dictionary built from Wiktionary, which includes articles, plurals, full conjugations and English meanings.
- **Your words are stored only on your phone**, in the browser's storage (IndexedDB). Settings → Export/Import saves and restores a JSON backup.
- **Claude lookup is optional.** If you add a Claude API key in Settings, an "Ask Claude" button appears for tricky words.

## Files

| Path | Purpose |
|---|---|
| `index.html` | The whole app |
| `manifest.webmanifest`, `icons/` | Home Screen name and icon |
| `sw.js` | Offline support (caches the app and the dictionary) |
| `tools/build_dict.py` | Builds the offline dictionary into `dict/` |
| `dict/` | Generated dictionary. Not included until you build it |

## 1. Build the dictionary (once, on your Mac)

Open **Terminal**, go to this folder and run:

```bash
cd ~/Claude/Wortschatz        # or wherever this folder is
python3 tools/build_dict.py
```

**What the script does:**
- Downloads the German data from kaikki.org (about 1 GB) and a word-frequency list into `build-cache/`.
- Keeps only German nouns and verbs.
- Writes `dict/nouns.json`, `dict/verbs.json`, `dict/meta.json` and `dict/ATTRIBUTION.txt`.

It needs only Python 3 and `curl`, both already on macOS, and takes a few minutes. At the end it prints how many words it kept and how big the files are.

**Useful options:**
- `--max-nouns 30000 --max-verbs 8000` keeps only the most frequent words. Use this if a file is over 25 MB, which is GitHub's web-upload limit.
- `--input FILE` uses a file you downloaded yourself.
- `--source raw` uses the full all-languages dump (about 2.7 GB). The script also switches to this automatically if the German file has been removed from kaikki.org, since that file is marked deprecated.

After the build you can delete `build-cache/`.

## 2. Deploy on GitHub Pages (free)

### Fastest option: one command in Terminal

```bash
cd ~/Claude/Wortschatz
bash publish.sh
```

**What the script does:**
1. Installs the GitHub CLI (`gh`) with Homebrew if it's missing.
2. Opens your browser once so you can sign in to GitHub.
3. Creates the public repository `wortschatz` and uploads the files.
4. Switches on GitHub Pages and prints your app's URL.

To use a different repository name, run `bash publish.sh other-name`.

### Or by hand in the browser

1. On github.com, create a **public** repository, e.g. `wortschatz`.
2. Choose **Add file → Upload files**. Drag in `index.html`, `manifest.webmanifest`, `sw.js`, `README.md`, and the folders `icons`, `dict` and `tools`, then commit.
   - Don't upload `build-cache/`.
   - The web uploader rejects files over 25 MB. If a dictionary file is bigger, rebuild with the `--max-…` options.
3. Go to **Settings → Pages → Deploy from a branch**, choose `main` and `/ (root)`, and save.
4. After a minute or two, the app is at `https://<username>.github.io/wortschatz/`.

The repo contains no keys and none of your saved words. Those stay on your phone.

## 3. Install on iPhone

1. Open the URL in **Safari**.
2. Tap **Share → Add to Home Screen**.
3. Open the app from the Home Screen once while you're online, so the dictionary is saved for offline use.

The Home Screen app has its **own storage, separate from Safari**, so use only the Home Screen app.

**Optional Claude lookup:** get an API key at https://platform.claude.com → Settings → API keys and paste it in the app's Settings. API usage is billed separately from a Claude subscription.

## Updating

**The app:** edit the files, bump `VERSION` in `sw.js`, then upload the files again.

**The dictionary:** rerun the build script and upload the new `dict/` folder. The app picks it up on the next launch after that.

## Dictionary format

- **`nouns.json`**: each entry is `[word, genders, plurals, senses[], rank]`.
  - `genders`: in the order Wiktionary lists them, `m`/`f`/`n`, or `p` for plural-only nouns.
  - `plurals`: `"Äpfel"` or `"Wasser|Wässer"`. An empty string `""` means no plural; `"?"` means the plural isn't listed.
- **`verbs.json`**: each entry is `[infinitive, senses[], praesens, praeteritum, partizip, aux, flags, rank]`.
  - `praesens` and `praeteritum` are `"ich|du|er|wir|ihr|sie"` strings.
  - `aux`: `h`, `s`, `s,h`, or `s,(h)`. Brackets mean the auxiliary is rarely used.
  - `flags`: `s` = separable, `r` = reflexive-only.
- **Senses:** usage labels are written as a prefix, e.g. `"(reflexive) to get dressed"`.

## Saved-word format (schema v2)

**Noun:**

```jsonc
{ "id": "uuid", "type": "noun", "article": "der", "word": "Apfel", "plural": "Äpfel",
  "pluralNote": "", "meaning": "apple", "note": "", "example": "…", "exampleEn": "…",
  "source": "manual|wiktionary|claude|seed|import", "createdAt": "ISO", "updatedAt": "ISO" }
```

**Verb:**

```jsonc
{ "id": "uuid", "type": "verb", "infinitive": "anrufen", "meaning": "to call",
  "praesens": { "ich": "rufe an", … }, "praeteritum": { "ich": "rief an", … },
  "perfektAux": ["haben"], "partizip": "angerufen", "separable": true, "reflexive": false, … }
```

The Perfekt forms aren't stored. The app builds them for display from the auxiliary, the reflexive pronoun and the participle.

## Credits & licenses

- **Dictionary data:** Wiktionary contributors, extracted by [Wiktextract](https://github.com/tatuylonen/wiktextract) and published at [kaikki.org](https://kaikki.org). Licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
- **Word frequencies:** [FrequencyWords](https://github.com/hermitdave/FrequencyWords) by Hermit Dave, based on OpenSubtitles. Licensed CC BY-SA 4.0.
- **Your copy of the dictionary:** the generated `dict/` files keep the CC BY-SA 4.0 license.
