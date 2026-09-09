# Product Integration Plan — Three Blocks

Date: 2026-09-09
Status: **ratified** — all decision gates resolved in the 2026-09-09 grill session (record at the end); CONTEXT.md terms already updated. Implementation may start.

Source: product-owner prioritization (2026-09-09) over the existing M0–M13 implementation. Master spec `docs/SPEC.md`; domain language `CONTEXT.md`; hard rules `AGENTS.md`.

Order of execution: **Block 1 → Block 2 → Block 3.** Blocks are sequenced, slices inside a block are sequential vertical behaviors (TDD, spec §7/§8).

Explicit non-goals for all three blocks (product decision 2026-09-09): ECG, inferred blood pressure, public social feed, unvalidated "biological age" score. Somatriq imports valid measurements when they exist; it never fabricates them from insufficient signals.

Also out of scope here (backlog, not forgotten): Collector Trust Center (P0 in the original table but not in the chosen three blocks), live strain, physiological activation, weekly plans, capacity trajectory, hormonal context, external context.

---

## 0. Starting point (what exists and is reused — do not re-research)

- Idempotent batch ingest; vendor daily observations already in the catalog: `avg_hrv`, `recovery`, `strain`, `total_sleep_min`, `spo2_pct`, `skin_temp_dev_c`, `resp_rate_bpm`, `steps` (`system.metrics`, `packages/contracts/somatriq_contracts/observations.py`).
- Own versioned derived algorithms: `somatriq_rhr_v1` (`derived.daily_features`), `somatriq_hrv_rmssd_v1`, `somatriq_baseline_v1` (median + inclusive IQR), `somatriq_recovery_v1` (explainable: contributions are signed robust z′, missing inputs listed, score null until earned).
- `assemble_today` (`packages/analytics/somatriq_analytics/today_data.py`) is the single shared TODAY read used by both `GET /api/v1/metrics/today` and the morning brief — same numbers by construction. Sleep today comes from sleep-session windows (`_SESSIONS_SQL`); RHR from 5-minute bucket medians.
- Morning brief: deterministic text (`brief.py`), scheduler → `notifications.outbox` → telegram/ntfy. Always emitted; partial data marked with coverage (grill decision 6).
- Journal: `health.journal_events` — kinds `journal|caffeine|training|experiment_checkin|note`, sources `telegram|web|api`, structured payload (quantity only when literally stated), verbatim text kept. `caffeine_count` is already a daily correlation feature.
- Deterministic correlations (Pearson/Spearman, honest skips, n ≥ 14 gating) and N-of-1 experiments (intervention, check-ins, compliance yes/no/unknown).
- Strength training M12: sessions, tonnage/hard sets, personal response with 1-day lag.
- Web: single dashboard page (today/daily/heart-rate/correlations/experiments/training/data/devices cards) + login + pairing. No `/explore` route yet. Static export CSR (ADR 0014).
- Known debt: `system.algorithms` registry does not exist — migration 0005 explicitly deferred it ("lands with its own later migration"). Block 1 pays this off.
- Next migration number: **0012**.

Hard rules that bind every slice: ADR 0009 (Python calculates, LLM explains), ADR 0012 (derived values versioned; same-version upsert refresh, never silent rewrite), ADR 0017 (local day + wake-date night attribution + effective timezone per row), ADR 0013 (all sources preserved), spec §158/§221 (never hide data-quality problems).

---

## Block 1 — Today Plan + Sleep Need

Goal: connect recovery to a same-day action. Every plan element is a Deterministic Derived Metric with explicit contributions, evidence label `Calculated`, and honest caveats. No LLM in the calculation (ADR 0009).

### 1.1 Domain terms (add to CONTEXT.md as part of this block)

- **Sleep Need** — Derived Metric (`somatriq_sleep_need_v1`): minutes of sleep recommended for the coming night, from personal baseline + recent debt + recent load + recovery state. Always lists contributions and missing inputs.
- **Sleep Debt** — rolling cumulative shortfall of measured sleep vs the personal sleep baseline over a frozen window (7 days), capped; a Sleep Need input, never shown as a standalone scary number without its window and cap.
- **Today Plan** — Derived Metric (`somatriq_day_plan_v1`): the day's training guidance tier, personal target-strain range, and bedtime window, each with contributions and caveats. Always present; degrades with caveats (mirrors TODAY's always-present recovery).
- **Training Guidance** — tier vocabulary `rest | light | moderate | hard` derived from recovery and recent load. Guidance, never medical advice (spec §2: never present wellness analytics as diagnosis).
- **Daily Derived Value** — one row per (local date, algorithm version) in `derived.daily_derived`; same-version upsert refresh semantics (ADR 0012).

Entity questions (spec §6) for `derived.daily_derived`:
- Real concept: the derived daily values a versioned Somatriq algorithm emitted per local day. Interpretation, not raw data.
- Lifecycle: owned by the compute path (read-through assembler persists what it showed); refresh = same-version upsert; version bump writes new rows.
- Change after creation: yes, via upsert refresh within a version (documented, `computed_at` moves); never across versions.
- Provenance: `algorithm_version` + input snapshot references inside `payload`; `timezone` on the row (ADR 0017).
- Multiple sources: no — single Somatriq algorithm per version; inputs may be multi-source (sleep source recorded in payload).
- Late data invalidates: recomputed on read; the persisted row reflects the latest read-through state (same trade-off as `daily_features`, documented).
- No `user_id` column, consistent with `derived.daily_features` (single-user deployment; IDs designed for later multi-user without tenant complexity — CONTEXT "User").

### 1.2 Algorithms (pure modules, TDD-first, DB-free)

`somatriq_sleep_need_v1` — `packages/analytics/somatriq_analytics/sleep_need.py`:

- Inputs: `baseline_sleep` (28-day median of measured sleep duration), `sleep_debt_7d` (Σ max(0, baseline − actual) over 7 days, capped at a frozen maximum; **asymmetric — no banking of credit**, ratified), `recent_load` (yesterday's vendor `strain`; optional), `recovery` (today's `somatriq_recovery_v1` score; optional).
- Output: `minutes` + per-input contributions + missing-inputs list. Additive form with frozen weights/caps clamped to [min, max]; all constants live in `packages/contracts/somatriq_contracts/plan.py` (frozen contract, same pattern as `recovery.py`). Sleep Need is a **prescriptive heuristic, never a Prediction** (ratified); the declared evolution path is shadow-scoring need-vs-actual toward a possible v2 `Predicted`.
- Measured sleep duration source (**ratified**): sleep sessions are canonical — summed per wake date exactly as `assemble_today` shows; `total_sleep_min` only fills wake dates without sessions, with the chosen source recorded; a large session-vs-daily discrepancy is a visible caveat, never a silent resolution.

`somatriq_day_plan_v1` — `packages/analytics/somatriq_analytics/day_plan.py`:

- Inputs: recovery (score or null), sleep need, 28-day personal strain distribution, sleep debt, baseline warmup state.
- Output: tier (`rest|light|moderate|hard`), target strain range (frozen percentiles of the 28-day personal strain distribution per tier, **anchored in vendor strain with the source visible** — ratified; `day_plan_v2` will re-anchor to Somatriq's own load metric and both versions run in parallel, ADR 0012; null while < 14 strain days), bedtime window (`wake_time − sleep_need ± 15 min`; null without a sleep need), contributions[], caveats[].
- `wake_time` (**ratified**): a **User Preference** stored in `identity.user_preferences` (`wake_time` key, typed `time`, documented default 07:00 local when unset); the plan records whether preference or default was used.
- Warmup: baselines still building → plan still emitted with explicit "building baselines (n days)" caveat.
- Never medical language; every recommendation traceable to its inputs (spec §76 explainability pattern).

### 1.3 Persistence & algorithm registry (migrations)

- `0012_user_preferences.py`: `identity.user_preferences (user_id uuid REFERENCES identity.users(id), key text, value jsonb NOT NULL, updated_at timestamptz, PRIMARY KEY (user_id, key))`. One typed key-value store for all preferences; `wake_time` now, `journal_reminder_time` in Block 2 (amended below); per-key typed validation at the API layer (registered pydantic schemas), never in the DB.
- `0013_system_algorithms.py`: `CREATE TABLE system.algorithms (name text PRIMARY KEY, description text, constants jsonb, created_at timestamptz)`. Seed with every existing algorithm (`somatriq_recovery_v1`, `somatriq_baseline_v1`, `somatriq_hrv_rmssd_v1`, `somatriq_rhr_v1`) + the two new ones. Pays the documented 0005 debt; Explore (Block 3) reads it for version markers.
- `0014_daily_derived.py`: `derived.daily_derived (date date, algorithm_version text, timezone text, payload jsonb NOT NULL, computed_at timestamptz, PRIMARY KEY (date, algorithm_version))` — **ratified: created now, latest-state semantics** (same-version upsert like `daily_features`; the as-sent brief already persists in `notifications.outbox` payloads).

### 1.4 API + brief

- `GET /api/v1/plan/today` (new router `services/api/somatriq_api/plan.py`, account JWT or read-scoped device token — same guard family as today.py). Response in `somatriq_contracts/plan.py`: date, timezone, tier, target_strain, bedtime_window, sleep_need {minutes, contributions}, recovery summary, contributions, caveats, as_of, coverage.
- `GET/PUT /api/v1/preferences` — read/write the typed preference store (account JWT only; unknown keys or invalid values → 422 with the registered schema).
- Assembler `plan_data.py` in analytics: single shared read used by the endpoint AND the brief (same-numbers-by-construction pattern as `assemble_today`). Persists the emitted plan/sleep-need rows to `derived.daily_derived`.
- Morning brief gains a Plan section (`brief.py` extension): tier + bedtime + one-line why; the brief's data-quality line already carries coverage.

### 1.5 Web

- "Today plan" section inside the today card (split into its own `plan-card.tsx` if the design review says so): tier, target strain, bedtime window, sleep need, expandable contributions with evidence labels. Plus a minimal Preferences surface (wake time) — same design review. Mandatory design review (design-taste-frontend; spec §14/§17, §221 "no major UI without design taste review").

### 1.6 Mobile (fork repo — contract only)

- The fork renders the plan in its server-backed science views. Contract: the endpoint above; no fork-side computation.

### 1.7 Vertical slices (each: RED → GREEN → REFACTOR, commit per slice)

1. `test_sleep_need_math.py` → `sleep_need.py` (baseline-only, capped debt, load bump, missing inputs, clamps, contributions).
2. `test_day_plan_math.py` → `day_plan.py` (tier thresholds, percentile strain ranges, warmup, degraded plan, bedtime math).
3. Migrations 0012 + 0013 + 0014 (up/down tests).
4. `plan_data.py` assembler (28-day sleep/strain/debt reads; canonical sleep source resolution) + integration tests against Timescale.
5. `GET/PUT /api/v1/preferences` + `GET /api/v1/plan/today` + contracts + integration tests (auth, honest empty-data, caveats on partial coverage).
6. Brief Plan section + scheduler tests.
7. Web UI (plan section + preferences) + `npm run typecheck && npm run build` + E2E smoke of the dashboard flow.
8. Docs: CONTEXT.md terms, README bullet, wire contract note in `docs/contracts/README.md` if shape changes.

Acceptance: the morning brief contains an explained plan; `/plan/today` returns 200 on seeded data with contributions and caveats; every number traces to a frozen constant or an input; no LLM anywhere in the path; `derived.daily_derived` accrues one row per algorithm per day.

---

## Block 2 — Journal as laboratory + transparent Behavior Insights

Goal: fast behavior logging (mobile-first, offline-tolerant) and honest per-behavior impact estimates that reuse the existing statistics machinery and funnel into experiments.

### 2.1 Domain terms (CONTEXT.md)

- **Behavior** — a journal vocabulary kind a user can quick-log: `caffeine, alcohol, medication, stress, meal, travel`. An event with `ts` (occurrence time), optional structured payload (quantity only when literally stated — caffeine precedent — never invented), verbatim text kept for provenance.
- **Behavior Insight** — Derived analysis (`somatriq_behavior_insight_v1`): association between exposure on day d (≥1 event of the kind) and an outcome on day d+1, reported with n per group, median difference, Mann-Whitney U p, Benjamini-Hochberg q (family = kinds × outcomes), confounders, and a method string. Evidence label `Associated`. Never causal language; below the n-gate it renders "keep logging" instead of a number.
- **Journal Reminder** — opt-in evening notification (scheduler → outbox → channel) prompting the day's quick-log.

### 2.2 Migration `0015_journal_vocabulary.py`

- `journal_events_kind_check` += `alcohol, medication, stress, meal, travel` (ratified P7: the six binary behaviors).
- `journal_events_source_check` += `mobile` (ratified P10: the fork logs with its own provenance identity).
- `outbox_kind_check` += `journal_reminder`.
- New optional column `client_event_id uuid` + `UNIQUE (user_id, client_event_id)` for offline retry idempotency (ADR 0006 spirit applied to journal writes).

### 2.3 Journal API
- `POST /api/v1/journal/events` — account JWT (web) or device token with `journal.write` scope (ratified P10: extend `DEVICE_SCOPES` + idempotent backfill, pattern of migration 0011; the collector never holds the account JWT). Per-kind structured validation in `somatriq_contracts/journal.py`.
- `GET /api/v1/journal?date=` — the local day's events (grouped by kind, newest first).
- `DELETE /api/v1/journal/events/{id}` — owner correction, physical delete (ratified P11): allowed on user-authored kinds only (`journal`, `note`, behaviors); system kinds (`training`, `experiment_checkin`) rejected with an explicit error; export reflects deletion; insights recompute on demand.

### 2.4 Behavior Insights (`somatriq_behavior_insight_v1`, pure module + TDD)

- `packages/analytics/somatriq_analytics/behaviors.py`; outcomes ∈ {`avg_hrv`, `recovery`, `total_sleep_min`, `resting_hr`}; exposure = any event of the kind on the prior local day; windowed variant for caffeine: `caffeine_after_14` (frozen constant 14:00 local).
- Gate: ≥ 7 exposed and ≥ 7 unexposed days (frozen contract constant), else an honest skip row ("keep logging — n=…/7").
- `GET /api/v1/journal/insights?days=90` — read-through, no persistence (deterministic and cheap at 90 days).
- UI copy rule: "associated with", never "improves/causes"; method string visible (expandable).

### 2.5 Convert to experiment
- Web: each insight renders "Convert to experiment" → prefilled experiment creation (intervention = behavior + window, outcome metric, direction). Reuses the existing experiments API verbatim; no schema change (no `inspired_by` column — the experiment's notes reference the insight text; revisit only if provenance demands it).
- Telegram: `/experiment` prefill command if the existing flow accepts parameters without changes; otherwise skip (keep Telegram surface stable).

### 2.6 Reminders

- Scheduler job: at the user's `journal_reminder_time` **User Preference** (amended: moved from env to the Block 1 preferences store; documented default 21:30 local; opt-in per channel), enqueue `journal_reminder` to outbox; notifications service drains (existing path). Tests: scheduler emission, drain, opt-out.

### 2.7 Surfaces

- Web: journal card — behavior chips (one tap = one event), day timeline, logging-streak/habit stats (Calculated), insights view with method + confounders, convert button.
- Fork (separate repo, offline-first): quick-log screen with the same chips + free note; queued idempotent POST via `client_event_id`; local reminder fallback when no channel is enabled. Contract defined by 2.2/2.3 — no server change needed beyond it.

### 2.8 Vertical slices

1. Migration 0015 (up/down tests).
2. `contracts/journal.py` per-kind validation + tests.
3. Journal API (POST/GET/DELETE, idempotency, auth incl. device scope) + integration tests.
4. `test_behaviors_math.py` → `behaviors.py` (grouping, lag, gating, FDR, confounders, windowed caffeine).
5. `/journal/insights` + integration tests (seeded journal + observations; honest skips).
6. Reminder scheduler + outbox + tests.
7. Web journal + insights + convert-to-experiment + typecheck/build + E2E.
8. Telegram quick-log parity for new kinds + tests.
9. Docs: CONTEXT.md terms, README, `docs/contracts/README.md` journal shapes.

---

## Block 3 — Explore + Health Monitor

Goal: make the longitudinal dataset a first-class tool — years of history with visible gaps, instrument boundaries and algorithm versions — plus a private vitals-vs-baseline monitor with a shareable, non-diagnostic PDF.

### 3.1 Explore (new `/explore` route, ADR 0014)

API (read-only, owner-scoped):

- `GET /api/v1/explore/series?metrics=a,b&from&to` — day grain from the existing daily stores (`derived.daily_features`, vendor dailies, journal-derived flags). Absent-day-honest (gaps stay gaps); per-day coverage; per-metric provenance summary (dominant source + device over the window); max span 3 years (frozen constant; longer → 400 with guidance). Week/month/year aggregation happens client-side from day grain (≤ ~1100 points), keeping one honest server contract.
- `GET /api/v1/explore/context?from&to` — overlays: device boundaries (active_from/active_to), active algorithm versions (`system.algorithms` + first-seen dates from `derived.*`), journal event kinds present, training sessions, timezone changes (distinct `daily_features.timezone`).

**Annotation** — new entity, migration `0016_annotations.py`: `health.annotations (id uuid PK, user_id, date_from date, date_to date NULL, title text, note text, created_at, updated_at)`. Entity answers: user-authored content, not derived; no algorithm version; mutable by design (edit/delete); provenance = author + timestamps; never invalidated by late data; **user-only — system facts stay computed overlays** (ratified); CRUD `GET/POST/PATCH/DELETE /api/v1/annotations`.

UI (two design-reviewed stages):

1. Timeline: multi-year line/area per metric with shaded gaps, device-boundary markers, algorithm-version markers, annotation pinning.
2. Month calendar heatmap of the chosen metric + period compare v1 (two windows: medians side by side, Calculated, no statistical tests — those live in correlations) + annotation create/edit.

### 3.2 Health Monitor (private)

- `packages/analytics/somatriq_analytics/health_monitor.py` (pure, TDD): per vital ∈ {hrv (computed when present, else vendor `avg_hrv`), `resting_hr` (computed), `resp_rate_bpm`, `spo2_pct`, `skin_temp_dev_c`} over 30/90/180 days: period median, personal baseline (`somatriq_baseline_v1`), robust deviation, status `within | elevated | reduced` (frozen z thresholds), coverage, warmup state, provenance (source + device). Deviations are phrased "vs your baseline", never "abnormal" (not a medical device).
- `GET /api/v1/health-monitor?days=30|90|180` — vitals + caveats + permanent non-medical disclaimer.
- `GET /api/v1/health-monitor/report.pdf?days=30|180` — server-rendered PDF from the API service (static-export web cannot do it), StreamingResponse. Library `[DECISION D8 — default: reportlab (permissive license, pure Python); verify license + release age at install time]`. Content: period, per-vital table (median / baseline / deviation / coverage / source), disclaimer block, provenance footer. Tests assert key strings, PDF magic bytes, and the presence of the disclaimer; a frozen forbidden-terms list (diagnose, disease, etc.) is asserted absent.
- Web `/health-monitor`: vital cards vs baseline with coverage + provenance, PDF export link; dashboard gains entry links to Explore and Health Monitor.
- Fork: server-backed view over the same endpoint (contract only).

### 3.3 Vertical slices

1. `explore/series` + tests (gap honesty, coverage, span limit, auth).
2. `explore/context` + tests (device boundaries, algorithm versions).
3. Migration 0016 + annotations CRUD + tests.
4. Explore UI stage 1 (timeline + gaps + overlays) + design review.
5. Explore UI stage 2 (calendar heatmap + compare + annotations) + design review + E2E.
6. `test_health_monitor_math.py` → `health_monitor.py` (baseline comparison, warmup, coverage, source preference).
7. `/health-monitor` + tests.
8. PDF report + tests.
9. Health Monitor UI + dashboard links + E2E.
10. Docs: CONTEXT.md (Annotation, Explore, Health Monitor, Vital), README, screenshots refresh.

---

## Decisions ratified — grill session 2026-09-09 (`docs/grill/` process; CONTEXT.md updated same session)

| # | Decision | Resolution |
|---|----------|------------|
| P1 | Epistemic status of Sleep Need | **Prescriptive heuristic + declared evolution** — `Calculated` in v1; shadow-scoring need-vs-actual is the declared path to a possible v2 `Predicted` |
| P2 | Sleep-duration source | **Sleep sessions canonical** (same number the Today card shows); `total_sleep_min` fills session-less wake dates, source recorded; discrepancy = visible caveat |
| P3 | Sleep Debt model | **Rolling 7-day, asymmetric (no banking), capped**; shown only with its window and cap |
| P4 | Plan persistence | **`derived.daily_derived` generic store, latest-state** (same-version upsert); as-sent brief already persists in outbox payloads |
| P5/P5b | Wake time + preference store | **BD + UI** (owner's choice, overriding env default); `identity.user_preferences` typed key-value store; Block 2's reminder time moves here |
| P6 | Training Guidance anchor | **Tier + target range from personal 28-day vendor strain distribution** (source visible); v2 re-anchors to own load metric, versions run in parallel |
| P7 | Behavior vocabulary | **6 binary kinds** (caffeine, alcohol, medication, stress, meal, travel) + verbatim note; quantity only when literally stated; no intensity scales |
| P8 | Behavior Insight vs glossary | **Insight definition sharpened** (ranking/AI text = presentation layers); Behavior Insight is a legitimate Insight (`Associated`, method visible, never causal) |
| P9 | Insight test family | **4 fixed outcomes, lag-1 only**; FDR family = 6 kinds × 4 outcomes = 24 tests, declared in the method string |
| P10 | Mobile journal auth | **Device-token scope `journal.write`** (pattern of migration 0011; fork never holds the account JWT) |
| P11 | Journal correction | **Physical DELETE, owner-only, user-authored kinds only**; system kinds (`training`, `experiment_checkin`) rejected explicitly; export reflects deletion |
| P12 | Explore series contract | **Day grain + context endpoint, 3-year cap, client-side aggregation**; one honest contract |
| P13 | Annotation entity | **User-only, optional date range**; system facts remain computed overlays |
| P14 | Health Monitor | **5 fixed vitals + deterministic PDF** (reportlab pending license/freshness check; fallback fpdf2); frozen forbidden-terms list asserted in tests |

Migration numbering after this session: 0012 user_preferences · 0013 system_algorithms · 0014 daily_derived · 0015 journal_vocabulary · 0016 annotations.

## Risks & mitigations

- Vendor sleep absent some days → fallback + honest n/caveats (D1); never impute.
- Spurious behavior "insights" → n-gating + BH-FDR + confounder reporting + association-only language.
- CHECK-constraint migrations → hand-written SQL, up/down tested (grill B3 discipline).
- PDF provenance drift → report generated only from the assembler's structured output; no free-form model text (ADR 0009).
- Explore over-querying → day-grain only, 3-year span cap, HTTP caching (spec §184: no new infrastructure).

## Definition of done per block (spec §192, §215)

Behavior + tests + types + lint + failure paths + logs/metrics where relevant + security review (token scopes, no new public surface; PostgreSQL stays private) + migrations up/down + docs (CONTEXT.md, README, contracts) + design review for UI + browser E2E for the critical flow + self-review checklist before declaring the block complete.
