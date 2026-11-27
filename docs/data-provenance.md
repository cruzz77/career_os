# Data provenance: job postings

**What the job index contains:** public postings from company job boards hosted on Lever (and Greenhouse).
It is not a licensed dataset, and must never be described as one in the README, the UI, this repo or a resume.
CareerOS is a non-commercial portfolio project.

Sources verified on 2026-11-27 against each provider's official documentation. This is a reading of
published terms, not legal advice.

---

## 1. Sources in use

| | Lever Postings API (primary) | Greenhouse Job Board API (second adapter) |
|---|---|---|
| Docs | https://github.com/lever/postings-api | https://docs.greenhouse.io/job-board.html (redirected from developers.greenhouse.io) |
| List endpoint | `GET https://api.lever.co/v0/postings/{site}?mode=json&skip=N&limit=N` (EU instance: `api.eu.lever.co`) | `GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true` |
| Single posting | `GET https://api.lever.co/v0/postings/{site}/{posting_id}` | `GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}` |
| Auth | None for GET. Keys exist only for submitting applications, which CareerOS never does | None for GET. The Basic-auth key is only for submitting applications, which CareerOS never does |
| What the docs say about the data | The API is designed to help build a job site. All postings in the `published` state are publicly viewable and may be scraped by third parties; other postings are hidden from the API | Job Board data is publicly available, so GET endpoints need no authentication. The API exists to build custom careers pages |
| Explicit data licence | None. Lever's Terms of Service (last updated 2023-08-25) govern Lever's customers and bar Lever's direct competitors; they do not address third-party display of published postings. CareerOS is not an ATS | None. The Job Board API page states no rate limits, caching rules or terms of use |
| Why chosen | Structured `workplaceType`, `categories.commitment` and ISO `country` fill the hard-filter columns with stated values. Full plain-text descriptions | Volume (Lever alone yielded 974 < 2,000 active jobs). Full descriptions, `first_published`, `application_deadline`, and `meta.total` for completeness checks |

## 2. Sources considered and rejected

| Source | Decision | Reason |
|---|---|---|
| Remotive public API (https://remotive.com/remote-jobs/api) | Rejected | Its terms forbid using Remotive jobs to collect signups or email addresses, and CareerOS requires an account. They also forbid passing its jobs on to other job sites |
| Adzuna API (https://developer.adzuna.com/docs/terms_of_service) | Not used | The only candidate with an explicit grant (publishing listings; personal or academic research), but: each displayed listing needs an "Adzuna" logo of at least 116 x 23 px linked to Adzuna, the free tier is for evaluating the API, all Adzuna data must be removed on termination, and the docs confirm neither India coverage nor full descriptions |

## 3. Conditions of use (blueprint §6.4.1)

- **Published postings only.** Only what the public list endpoints return is stored.
- **Link back on every card.** `jobs.source_url` holds Lever's `hostedUrl` or Greenhouse's `absolute_url`.
  Every job card shows the company and the source and links to the original posting. Applications always
  happen on the employer's own page.
- **No bulk export** endpoint and **no re-syndication** to other job boards.
- **Takedown.** The README lists a takedown contact. A request is honoured by removing the company's slug from
  its config file and deleting its rows.
- **Synthetic data** for tests is flagged `jobs.is_synthetic = true` and is never shown as a live job.

## 4. Fetching policy

- Public, unauthenticated GET only.
- At most 1 request/second per host, with a descriptive `User-Agent` that includes a contact address.
- Retries with exponential backoff (honouring `Retry-After`) on 429, 5xx and transport errors.
- Sync runs **hourly** (blueprint v0.3.4 §6.4.2, superseding the earlier every-6-hours limit). That is one
  list request per company per hour: about 22 requests an hour in total.

## 5. Freshness and removal (blueprint §6.4.2)

- **Complete fetch.** For Lever, a fetch counts as complete when every page returned HTTP 200, parsed, and the
  last page was short. For Greenhouse, it is complete when the number of jobs received equals `meta.total`.
- **Authoritative miss.** Only a complete fetch can expire jobs: postings absent from it are set to
  `expired` with reason `missing_from_source`.
- **Board-level 404.** A 404 on a list endpoint means the board was removed or renamed. That fetch is
  incomplete and expires nothing. The 24-hour stale sweep covers it.
- **Safety valve.** If one fetch would expire more than 50% of a slug's active jobs (and more than 5 jobs),
  expiry is skipped for that slug and an anomaly is logged.
- **Live check.** The single-posting endpoint returns 404 for both an unknown posting and an unknown company
  on both sources. Only 404 or 410 with the documented JSON error body counts as `not_found`. Anything else is
  `error`, which never expires a job.
- **Other expiry routes.** `expires_at` comes from Greenhouse `application_deadline`; Lever has no expiry
  field. Jobs past `expires_at` are expired (`past_expires_at`). Jobs not seen for 24 hours are expired
  (`stale_not_seen`). A job that reappears is reactivated.

Endpoint behaviour measured on 2026-11-27 (one request each, from the developer's machine in India):

| Probe | Result |
|---|---|
| Lever real posting | 200, about 1.5 s |
| Lever unknown posting id, malformed id, unknown site | 404 `{"ok":false,"error":"Document not found"}` (0.5–1.8 s) |
| Greenhouse real job | 200, about 0.4 s |
| Greenhouse unknown job id, unknown board | 404 `{"status":404,"error":"Job not found"}` (about 0.3 s) |
| Greenhouse list, with and without `content=true` | 200, includes `meta.total` |

## 6. Company selection

Slugs live in versioned config files, never in code:
`backend/app/modules/ingestion/sources/lever_companies.yaml` and `greenhouse_boards.yaml`.
They were chosen by volume-check runs on 2026-11-27. Candidates were guessed, and the run kept only those
that exist: 11 of 72 Lever candidates and 33 of 68 Greenhouse candidates. The Greenhouse boards were then
narrowed for India coverage (decision v0.3.5, option B). Adding a company is a config change plus a line
in this table.

| Source | Company (slug) | Postings on 2026-11-27 |
|---|---|---|
| Lever | palantir | 305 |
| Lever | zoox | 246 |
| Lever | paytm | 197 |
| Lever | spotify | 97 |
| Lever | meesho | 60 |
| Lever | zeta | 38 |
| Lever | mindtickle | 17 |
| Lever | cred | 10 |
| Lever | binance | 4 |
| Lever | freshworks, kraken | 0 (board exists, nothing published) |
| Greenhouse | databricks | 882 |
| Greenhouse | mongodb | 421 |
| Greenhouse | okta | 360 |
| Greenhouse | gitlab | 241 |
| Greenhouse | rubrik | 223 |
| Greenhouse | twilio | 113 |
| Greenhouse | inmobi | 64 |
| Greenhouse | hackerrank | 32 |
| Greenhouse | druva | 24 |
| Greenhouse | observeai | 10 |
| Greenhouse | groww | 9 |
| **Total** | | **3,353** (Lever 974, Greenhouse 2,379) |

India share: Lever 312 of 974 (32%, from the ISO `country` field). Across both sources about 21%. The
Greenhouse part is estimated by matching Indian city names in the location text, so it is approximate.

## 7. Field mapping rules

Hard-filter columns (`work_mode`, `employment_type`, `seniority`, location) are only filled from values the
source states. A wrong value would break the zero-violation guarantee; `unknown` is shown as "not specified".

- **Lever.** `workplaceType` maps to `work_mode` (observed values: onsite, hybrid, remote).
  `categories.commitment` maps to `employment_type` through an exact table over normalised values: 19 raw
  spellings observed, unmapped values become `unknown`. `country` gives the ISO country.
- **Greenhouse.** Values come only from (a) a board's structured custom fields, for example mongodb
  `Employment Type` and `Working Model Eligibility`, or inmobi `Employment Type` and `Base Country`; and
  (b) an explicit work-mode token in the location string, for example `Hybrid in Bangalore, India` or
  `Remote - India`. Each goes through a per-board, exact-match table. If a multi-location string mixes
  modes, the result is `unknown`.
- **Seniority** comes from Lever `categories.level` where it is stated, otherwise from a conservative title
  heuristic. A title maps to a level only when it names one unambiguously (intern; junior / jr / new grad;
  senior / sr / staff / principal / director / head / vp / chief). Everything else is `unknown`; on
  2026-11-27, 35.6% of titles had no level token at all.
- **Never used:** inferring any of these fields from the description text, by keyword search, or with an LLM.
- **Logging:** every raw value that no table maps is logged and counted in `ingestion_runs.notes`.
- **Description:** Lever's `descriptionPlain`, `lists` and `additionalPlain` are joined, because
  `descriptionPlain` alone was empty on 6.3% of postings. For Greenhouse, `content` is HTML-unescaped and
  stripped of tags. A posting whose resulting text is empty is counted as `failed` and not stored.
- **`posted_at`** comes from Lever `createdAt` or Greenhouse `first_published`.
- **Not stored:** salary fields (CareerOS has no compensation dimension), application questions, compliance
  fields, and anything about candidates.

## 8. Known limitations

- Neither source grants an explicit licence. Use relies on the postings being published publicly for display,
  together with the conditions in section 3.
- Coverage is limited to the companies in the config files. It is a sample of public boards, not the job market.
- Location strings are free text with inconsistent formats, so city and country parsing is best effort.
  Unparsed locations are stored as unknown, not guessed.
- Lever's single-posting endpoint took 1.5–1.8 s from India against a 2 s live-check timeout. Latency from the
  production VM is to be measured before changing that limit.
