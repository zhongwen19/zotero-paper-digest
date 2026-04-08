# zotero-paper-digest

A small GitHub-first daily paper recommendation job for one researcher. It reads seed papers from configured Zotero collections, discovers related public scholarly metadata, ranks candidates with cheap local heuristics first, optionally reranks a small shortlist with DeepSeek, and emails a concise digest.

It does not write to Zotero. You decide manually whether to import recommended papers.

## What It Does

- Reads metadata from one or more Zotero collections: title, abstract, DOI, year, venue, authors, tags, and URL.
- Discovers candidate papers from OpenAlex first and Crossref as a lightweight optional source.
- Prefers recent papers in a configurable window, for example the last 90 days.
- Falls back to classic papers when not enough high-quality recent papers are found.
- Deduplicates by DOI, normalized title, and external IDs.
- Scores candidates locally before any LLM call.
- Optionally sends one batched DeepSeek rerank call for only the shortlist.
- Sends an HTML and plain-text email digest.
- Saves generated digest files and logs as GitHub Actions artifacts.

## Quick Start With GitHub Actions

1. Create a GitHub repository named `zotero-paper-digest`.
2. Add this project to that repository.
3. In GitHub, open `Settings -> Secrets and variables -> Actions -> New repository secret`.
4. Add these secrets:

```text
ZOTERO_LIBRARY_ID
ZOTERO_API_KEY
ZOTERO_COLLECTION_KEYS
DEEPSEEK_API_KEY
SMTP_HOST
SMTP_PORT
SMTP_USER
SMTP_PASS
EMAIL_FROM
EMAIL_TO
```

`ZOTERO_COLLECTION_KEYS` should be comma-separated, for example:

```text
ABC123,DEF456
```

5. Open `.github/workflows/daily_digest.yml`. The default schedule is `30 1 * * *`, which is 09:30 in Asia/Shanghai because GitHub cron uses UTC.
6. Push to GitHub.
7. Open the Actions tab and run `Daily Zotero Paper Digest` manually with `workflow_dispatch` to test it.

## Zotero Setup

Create a Zotero API key with read access to the library that contains your seed collections.

For a personal Zotero library:

```yaml
zotero:
  library_type: user
```

For a group Zotero library:

```yaml
zotero:
  library_type: group
```

The library ID and collection keys are normally supplied through GitHub Secrets rather than committed to the repository.

## Configuration

Edit `config.yaml` for non-secret settings. `config.example.yaml` is kept as a template:

```yaml
zotero:
  library_type: user
  library_id: ""
  collection_keys: []
  max_seeds: 30

discovery:
  recent_days: 90
  max_candidates_per_source: 50
  sources: [openalex, crossref]

ranking:
  shortlist_size: 25
  llm_enabled: true
  llm_max_items: 25
  prefer_new: true
  min_new_results_before_classics: 3

classics:
  min_age_years: 5

email:
  from_email: ""
  to_email: ""
  smtp_host: ""
  smtp_port: 587

output:
  save_json: true
  save_html: true
```

Environment variables override these fields where appropriate. For example, GitHub Secrets override `ZOTERO_LIBRARY_ID`, `ZOTERO_COLLECTION_KEYS`, SMTP settings, and email addresses.

## Recommendation Logic

The system is designed for precision over recall. It may send only a few papers on quiet days.

Local ranking considers:

- Keyword and concept overlap with the seed collection.
- Venue overlap.
- Author overlap.
- OpenAlex related-work proximity when available.
- Recency boost for new papers.
- Citation count as a quality signal, especially for classic papers.
- Metadata completeness, such as DOI, abstract, and venue.

DeepSeek reranking is optional and intentionally small. When enabled, the job sends only the shortlist and a compact seed summary. It does not run an agent loop, does not call tools, and does not fetch full text.

To disable DeepSeek:

```yaml
ranking:
  llm_enabled: false
```

## Outputs

Each run writes:

```text
outputs/digest.json
outputs/digest.html
outputs/digest.txt
outputs/run.log
```

GitHub Actions uploads these as artifacts even if the job fails after partial output generation.

Logs include candidate counts, shortlist count, whether LLM rerank was triggered, and estimated token usage.

## Optional Local Debugging

The primary path is GitHub Actions. Local Python is optional.

If you do want to test locally:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m src.main --config config.yaml --output-dir outputs --skip-email
```

Set environment variables first, or copy `.env.example` into your shell environment manually. This project intentionally does not require a local database or Docker.

## Limitations

- OpenAlex `from_publication_date` approximates recent publication date. It is not the same as "newly indexed today."
- Crossref abstracts are often missing, so OpenAlex usually gives better ranking signals.
- No full text is fetched.
- No embeddings are used in the MVP.
- DeepSeek failures fall back to local ranking so the daily job can still complete.

## Development

Run tests:

```powershell
pytest
```
