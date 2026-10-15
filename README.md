# LawPilot

Legal research and practice management for advocates and law firms in India.

LawPilot combines a matter workspace (clients, hearings, tasks, notes, documents) with a
retrieval-augmented legal research engine that answers questions only from passages it can show you,
flags claims it cannot support, and says so when its library has nothing relevant.

## What it does

**Practice**
- Accounts with secure server-side sessions, password reset, profile, preferences, sign-out of other devices.
- Individual practice or a firm. Firms invite members by email/link with roles: Firm Admin, Partner, Associate,
  Junior Associate, Researcher, Clerk, Read-only. Firm Admins can change roles and grant/revoke individual permissions.
- Matters with team access (lead / can edit / view only) and confidential matters (ethical walls: hidden even from partners).
- Hearings / next dates, dashboard of the next 14 days, optional reminder emails.
- Tasks and help requests: assign research or drafting to a colleague, with due dates and priorities.
- Notes, document uploads (PDF, DOCX, TXT, HTML) indexed for research within that matter only.
- Searchable activity history, data export, account deletion requests.

**Research**
- Plain-language questions, Quick or Deep mode (Deep splits the question into sub-issues and double-checks every claim).
- Understands citations (SCC, SCR, AIR, INSC, neutral citations), sections, articles, Order/Rule, courts, judges,
  parties, time periods, and maps common IPC/CrPC/Evidence Act sections to BNS/BNSS/BSA.
- Hybrid retrieval: full-text search + vector search, fused and reranked by relevance, court hierarchy,
  jurisdiction, source class and legal status. Filters are applied before anything reaches the AI.
- Contrary-authority search, structured answers (applicable law, authorities, analysis marked as stated / inference /
  assumption, contrary authorities, limitations), and a check of every claim against the passage it cites.
- Clear outcomes: answered / not enough authority / nothing relevant in the library / search failed / passages only.
- Every research session records the exact source versions, embedding model and library snapshot used.

**Drafting**: nine Indian templates (Section 138 notice, arbitration notices and petitions, Article 226 writ,
commercial plaint, bail application, opinion, written submissions), pre-filing checks (empty fields, overruled
authorities, old vs new criminal codes, verification, Section 12A), versions, Word export, AI drafting help that
only cites authorities from your chosen research.

**Library administration** (platform admins): import real Supreme Court judgments, upload / URL-import / paste
sources, the official Indian Kanoon API, metadata and legal-status editing, versions, relationships
(overrules / follows), ingestion log, coverage dashboard, AI and embedding settings.

## Quick start (try it on your computer)

```bash
python -m venv venv
venv\Scripts\activate            # Windows  (source venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000 and create an account. **The first account becomes the platform administrator.**
This uses a SQLite file in `instance/`, which is fine for trying it out. For real use, set up PostgreSQL (below).

Then, as the administrator:

1. **Turn on AI answers**: *Corpus admin → AI settings*. Pick a provider, paste an API key, click
   **Test connection**, then **Save**. Without a key, research shows ranked passages but no written answer.
   - Google Gemini has a free tier (key from aistudio.google.com). Google may use free-tier prompts to improve its
     products, so do not send confidential client material through a free key.
   - Anthropic Claude (console.anthropic.com) is paid and the default (`claude-opus-5-5`); OpenAI, OpenRouter and Groq also work.
   - Click **Load available models** to pick from the models your key can actually use.
2. **Load real law**: *Corpus admin → Import judgments* imports real Supreme Court judgments (see below). Add bare
   Acts from India Code with *Add sources → Import from URL / Upload file*.

## Database: PostgreSQL

Set `DATABASE_URL` and LawPilot uses PostgreSQL with full-text search (GIN-indexed `tsvector`) and, if the
`pgvector` extension is available, vector search inside the database (HNSW index; `halfvec` on pgvector 0.7+).
Tables are created automatically on first start.

```
DATABASE_URL=postgresql://lawpilot:password@localhost:5432/lawpilot
```

Options that do not expire:

| Option | Cost | Good for |
|---|---|---|
| **Self-hosted with Docker** (`docker compose up -d`, see below) on your own computer, an office server or a VPS | Free / VPS rent | Recommended. Full control, room for the whole judgment collection. |
| **PostgreSQL installed directly** (postgresql.org installer + [pgvector](https://github.com/pgvector/pgvector)) | Free | Same as above, without Docker. |
| **Neon** free plan (neon.com) | Free, no expiry, about 0.5 GB | Getting started; holds a few hundred imported judgments. Has pgvector. Sleeps when idle (first query after a pause is slower). |
| Oracle Cloud *Always Free* VM running the Docker setup | Free (card verification) | A free always-on server with far more disk than free database plans. Check Oracle's current terms. |

Avoid free tiers that expire or pause (Render's free PostgreSQL expires after 30 days; Supabase pauses idle free projects).

**Self-hosting with Docker** (runs the app and PostgreSQL + pgvector together):

```bash
copy .env.example .env        # set POSTGRES_PASSWORD
docker compose up -d
```

Data lives in Docker volumes (`pgdata`, `appdata`). Back them up, e.g. `docker compose exec db pg_dump -U lawpilot lawpilot > backup.sql`.

If the app runs on a host with a temporary disk (some cloud web services), also set `LAWPILOT_SECRET_KEY` so sign-ins and
the encrypted API keys survive restarts. Uploaded files' text is stored in the database; only the original file
download needs the disk.

## Real legal data

**Supreme Court judgments** come from the public
[Indian Supreme Court Judgments](https://registry.opendata.aws/indian-supreme-court-judgments/) open dataset
(about 35,000 judgments from 1950 onwards, CC-BY-4.0, originally from the Supreme Court's judgment portal,
maintained at [github.com/vanga/indian-supreme-court-judgments](https://github.com/vanga/indian-supreme-court-judgments)).
Each imported judgment keeps its parties, SCR citation, neutral citation, coram, decision date, case number,
bench, headnote and full text with page/paragraph pinpoints, plus a link to the PDF. Imports are incremental:
re-running skips judgments already present.

- In the app: *Corpus admin → Import judgments* (runs in the background, shows progress, can be cancelled).
- From the command line (better for large imports): `python -m lawpilot.importers --years 2015-2024`

Storage: roughly 0.5–1 MB per judgment once indexed, so the full collection needs tens of GB (self-hosted PostgreSQL).
Old scanned judgments without a text layer are reported as failures rather than indexed as empty.
The dataset text is not the certified copy; verify against the court's record before relying on it.

**Acts and rules**: import PDFs from India Code (indiacode.nic.in) via *Add sources*. Mark a source "Verified against
official source" once checked.

**Starter summaries**: 66 hand-written summaries of landmark cases can be loaded for a demo
(*Coverage & index → Load*, or `LAWPILOT_SEED_SUMMARIES=1`). They are labelled as secondary sources and are off by default.

## Configuration

Environment variables (or a `.env` file; see `.env.example`). AI keys and embedding settings can also be set in the
app, which overrides the environment and stores keys encrypted.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string. Empty = SQLite file. |
| `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, ... | AI answers (or set in *AI settings*). |
| `EMBEDDING_PROVIDER`, `VOYAGE_API_KEY` | `voyage` (legal-domain `voyage-law-2`) gives true semantic search; re-embed after switching. |
| `SMTP_*` | Email for invitations, password resets, task and hearing notifications. |
| `LAWPILOT_BASE_URL`, `LAWPILOT_SECURE_COOKIES=1`, `LAWPILOT_SECRET_KEY` | Production behind HTTPS. |
| `KANOON_API_TOKEN` | Official Indian Kanoon API for importing individual judgments. |

Hearing reminders: schedule `python -m lawpilot.reminders` daily.

## Hosting on Vercel

Vercel deploys straight from GitHub and redeploys on every push. Use it with a hosted PostgreSQL (Neon), because
Vercel has no permanent disk. Limits to know:

- Requests stop after 5 minutes (Hobby plan): research is fine; run **judgment imports and large re-embeds from your
  computer** (`python -m lawpilot.importers --years 2024`) against the same `DATABASE_URL`. The results appear on the
  website immediately.
- Uploads are limited to about 4 MB per file. Larger documents need to be split.
- Uploaded files' text is stored in the database, so search works; downloading the original file of a matter
  document only works until the server instance restarts.
- In Vercel's environment variables set at least `DATABASE_URL`, `LAWPILOT_SECRET_KEY` (the **same** value as locally,
  otherwise the saved AI keys can't be decrypted), `LAWPILOT_BASE_URL` (your Vercel address),
  `LAWPILOT_SECURE_COOKIES=1`, `LAWPILOT_ADMIN_EMAILS`, and the `SMTP_*` settings.

## Running in production

```bash
gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 300
```

Keep one worker process: background imports and login throttling live in process memory.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest                                   # SQLite
set LAWPILOT_TEST_BACKEND=postgres && python -m pytest   # PostgreSQL (starts a throwaway server via pgserver,
                                                         # or set LAWPILOT_TEST_DATABASE_URL)
```

53 tests covering authentication, roles and permissions, ethical walls, matter-document isolation in retrieval,
research outcomes (fake LLM), claim verification, prompt-injection handling, ingestion, versioning, SSRF protection,
drafting checks, AI settings (keys encrypted, never returned), the judgment importer, and the PostgreSQL search paths.

## Honest limits

- Claim verification is lexical overlap plus citation checks (plus a model check in Deep mode). It catches invented
  citations and unsupported statements well, but it is an aid, not a guarantee. Read the authorities.
- The built-in embedder (`local-hash-v1`) is lexical-semantic, not neural. Use Voyage for real semantic search.
- High Court judgments are not bulk-imported yet (a companion open dataset exists; see next steps).

## Next steps for real-world use

1. High Court judgments importer (the [Indian High Court Judgments](https://registry.opendata.aws/indian-high-court-judgments/) dataset covers 25 High Courts).
2. Cause-list integration to pull next dates from eCourts automatically.
3. OCR on upload for scanned documents and old judgments.
4. Citator data (followed / overruled / distinguished) at scale.
5. Two-factor authentication for firms.

## Project layout

```
app.py                     entry point
lawpilot/
  __init__.py config.py    app factory, settings from environment
  db.py                    SQLite / PostgreSQL schema and connections
  settings.py              admin-editable AI settings (encrypted keys)
  auth.py rbac.py          sessions, roles/permissions, matter access
  extract.py chunking.py guard.py ingestion.py importers.py   document pipeline and bulk import
  embeddings.py query_analysis.py retrieval.py reranking.py research.py llm.py   research engine
  draft_templates.py       drafting templates and checks
  routes/                  HTTP API
static/                    web app (no build step)
tests/                     pytest suite
Dockerfile docker-compose.yml
```

LawPilot is a research aid. It does not replace reading the authorities or professional judgement.
