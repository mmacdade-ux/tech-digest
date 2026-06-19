# Tech digest

A daily tech-news digest, emailed via [Resend](https://resend.com) and built by
GitHub Actions — in the spirit of newsletters like Superpower Daily.

- **Schedule:** every day at 21:00 UTC (07:00 Australia/Brisbane) — see
  [`.github/workflows/digest.yml`](.github/workflows/digest.yml).
- **What it does:** [`scripts/build_digest.py`](scripts/build_digest.py) fetches
  RSS/Atom feeds + Hacker News + Reddit, deduplicates against `seen.json`, groups
  the new stories into sections, and emails an HTML newsletter (with automatic
  dark mode in Apple Mail). It also commits `latest.html` as a web archive.
- **Sources:** [`scripts/sources.py`](scripts/sources.py) — edit to add/remove feeds.

## Sections & sources

| Section | Sources |
|---------|---------|
| Top tech | The Verge, Ars Technica, Engadget |
| Apple | MacRumors, 9to5Mac |
| Hardware & reviews | RTINGS |
| Community buzz | Hacker News (front page, 100+ pts), Reddit (technology/gadgets/apple/hardware) |

## Setup (one-time)

1. **Resend:** sign up at resend.com **with mmacdade@me.com**, create an API key.
2. **Secret:** repo → Settings → Secrets and variables → Actions → New repository
   secret → `RESEND_API_KEY`.
3. **Actions write:** Settings → Actions → General → Workflow permissions →
   Read and write.
4. *(optional)* **Pages:** Settings → Pages → Deploy from branch `main` / root →
   archive at `https://mmacdade-ux.github.io/tech-digest/latest.html`.

## Run it yourself

```sh
python3 scripts/build_digest.py --dry-run   # build archive + email-preview.html, no send
python3 scripts/build_digest.py             # build + send (needs RESEND_API_KEY)
```

On demand in the cloud: Actions tab → **Daily tech digest** → **Run workflow**.

## Dedup

`seen.json` is keyed by a SHA-1 of each story's URL; entries are only added, so a
story is emailed exactly once. Only items published in the last 48h are considered.
