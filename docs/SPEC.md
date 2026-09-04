# SOMATRIQ

## Master Product, Architecture and Engineering Specification

Version: 2.0

Status: implementation source of truth

Target: self hosted personal biometric intelligence platform

Deployment platform: Dokploy on a private VPS

Mobile foundation: NOOP fork

License intention: noncommercial source available, compatible with the restrictions inherited from NOOP

---

# 1. PRODUCT MISSION

Build Somatriq as a personal health data operating system.

Somatriq is not merely a WHOOP dashboard.

Somatriq must own the entire useful lifecycle of personal biometric data:

```text
COLLECT
    ↓
PRESERVE
    ↓
NORMALIZE
    ↓
VALIDATE
    ↓
ANALYZE
    ↓
MODEL
    ↓
UNDERSTAND
    ↓
EXPERIMENT
    ↓
ACT
```

The fundamental asset is not the current wearable.

The fundamental asset is the user's longitudinal dataset.

Wearables can change.

Models can change.

LLM providers can change.

Frontend frameworks can change.

Somatriq's canonical historical dataset must remain usable.

---

# 2. CORE PRODUCT PRINCIPLES

The project must preserve these principles.

```text
Own your data.

Local first collection.

Self hosted intelligence.

Raw before interpretation.

Statistics before AI.

Explain every score.

No mandatory cloud.

No mandatory subscription.

No vendor lock in.

Everything exportable.

Everything traceable.

Everything versioned.

Deterministic computation before probabilistic explanation.

Never confuse correlation with causation.

Never present wellness analytics as medical diagnosis.
```

---

# 3. AGENT OPERATING CONTRACT

The development agent must treat this document as the primary specification.

Before modifying architecture, database contracts, security boundaries or domain concepts, inspect:

```text
CONTEXT.md

docs/adr/

docs/architecture/

docs/domain/

docs/security/

current database migrations

current API contracts
```

Do not silently contradict an existing ADR.

If an ADR needs to change:

```text
create superseding ADR
explain why
identify migration impact
update CONTEXT.md if terminology changed
```

Do not make architectural changes only because a new library appears attractive.

Prefer stability and coherent boundaries.

---

# 4. REQUIRED SKILL USAGE

The agent must actively discover the installed skills available in its environment before beginning substantial work.

Do not assume skill names exist.

Inspect available skills first.

When a matching skill exists, use it.

Skills are not decorative prompts.

They are part of the engineering process.

Load only skills relevant to the current task.

Do not load dozens of skills simultaneously because this pollutes context.

---

# 5. GRILL WITH DOCS

Before major irreversible design work use:

```text
grill-with-docs
```

or the corresponding installed equivalent.

This includes:

```text
new domain boundaries

database redesign

new synchronization protocol

authentication redesign

new wearable abstraction

AI architecture changes

new experiment model

major UX information architecture changes
```

The output must feed:

```text
CONTEXT.md
docs/adr/
```

Somatriq should maintain a shared domain language instead of letting terminology drift.

Examples:

```text
Observation
Measurement
Metric
Derived Metric
Source
Canonical Source
Raw Frame
Batch
Experiment
Intervention
Insight
Prediction
Baseline
Data Quality
Coverage
```

Every term should have one precise meaning.

---

# 6. DOMAIN MODELING

Use the available domain modeling skill whenever introducing or substantially changing health domain concepts.

Before adding a new database entity ask:

```text
What real domain concept does this represent?

Who owns its lifecycle?

Is this raw data or interpretation?

Can it change after creation?

Does it have provenance?

Does it have an algorithm version?

Can multiple sources provide it?

Can late arriving data invalidate it?
```

Avoid database tables that exist merely because they are convenient for one screen.

Model the domain.

Then build screens over it.

---

# 7. TDD IS THE DEFAULT ENGINEERING MODE

Use Test Driven Development for deterministic behavior.

Required loop:

```text
RED
↓
GREEN
↓
REFACTOR
```

Do not write twenty speculative tests first.

Build one vertical behavior slice.

Example:

```text
Test:
duplicate ingest batch must not create duplicate heart rate records

RED

Implement minimum idempotency logic

GREEN

Refactor

Next behavior
```

Tests should target stable public behavior rather than implementation details.

---

# 8. WHERE TDD IS REQUIRED

TDD is mandatory for:

```text
ingestion

synchronization

idempotency

authentication

authorization

source resolution

health calculations

HRV calculations

baselines

recovery calculations

training load

experiment calculations

data quality

late data handling

algorithm versioning

MCP tools

API behavior

notification rules

parsers

importers

exporters

job state machines
```

---

# 9. WHERE STRICT TDD IS NOT REQUIRED

Pure visual experimentation does not need a unit test written before every CSS change.

For UI use:

```text
component tests

interaction tests

accessibility tests

Playwright E2E

responsive tests

visual review

optional screenshot regression
```

Behavioral logic underneath UI still requires tests.

---

# 10. DIAGNOSING BUGS

For nontrivial defects use the installed diagnosing bugs skill.

Required process:

```text
reproduce

minimize

form hypothesis

instrument

identify root cause

write failing regression test

fix

verify

remove temporary instrumentation
```

Never patch a symptom if the root cause remains unknown.

---

# 11. CODE REVIEW

Every substantial feature should receive a separate review pass.

Review two dimensions independently:

```text
SPEC COMPLIANCE

ENGINEERING QUALITY
```

Questions:

```text
Does it actually satisfy the requested behavior?

Does it violate architecture?

Did it introduce hidden coupling?

Does it handle failure paths?

Are permissions correct?

Can data be duplicated?

Can data be lost?

Can it be replayed safely?

Is there an easier design?

Are tests meaningful?

Are docs updated?
```

---

# 12. RESEARCH SKILL

When implementing behavior based on:

```text
WHOOP protocol behavior

Android lifecycle behavior

Health Connect

Dokploy

TimescaleDB

PostgreSQL

MCP

LangGraph

FastAPI

security standards

new wearable integrations
```

use the available research skill or equivalent.

Prefer:

```text
official documentation

upstream source code

standards

primary repository

maintainer documentation
```

over blog summaries.

Record important findings under:

```text
docs/research/
```

when they affect long term architecture.

---

# 13. AGENT BEST PRACTICES

Use an agent architecture best practices skill when changing:

```text
tool architecture

MCP architecture

agent permissions

memory

prompt hierarchy

context building

model routing

tool discovery

approval flows

agent observability
```

The agent system must have explicit:

```text
source of truth

permission boundaries

tool schemas

execution logging

failure semantics

memory boundaries

context construction

evaluation strategy
```

Never give the LLM arbitrary infrastructure access because it is convenient.

---

# 14. DESIGN SKILLS ARE REQUIRED

Before implementing a new major frontend surface use one of the installed high quality design skills.

Preferred examples:

```text
design-taste-frontend

impeccable

design-taste

high-end-visual-design
```

Use only the relevant skill.

Do not force every aesthetic skill onto the same interface.

---

# 15. DESIGN OBJECTIVE

Somatriq must not look like:

```text
generic admin template

Grafana clone

AI generated SaaS

crypto dashboard

random gradient health app

collection of cards with no information hierarchy
```

It should feel like a serious personal instrumentation product.

Desired qualities:

```text
precise

calm

scientific

premium

legible

dense when needed

minimal when possible

high information hierarchy

strong typography

excellent spacing

excellent chart behavior

excellent mobile responsiveness
```

---

# 16. VISUAL PRODUCT PHILOSOPHY

Somatriq has two visual modes conceptually.

## Daily use

Simple.

```text
How am I?

What changed?

What matters?

What should I do?
```

## Exploration

Deep.

```text
raw signals

statistics

sources

quality

algorithms

experiments

model evaluation
```

Use progressive disclosure.

---

# 17. FRONTEND DESIGN REVIEW

Before declaring a page finished perform a dedicated design review.

Check:

```text
hierarchy

typography

spacing

density

contrast

responsive behavior

empty state

loading state

error state

keyboard navigation

touch targets

chart legibility

mobile behavior

dark mode

light mode

motion restraint

accessibility
```

The design review must be independent from the functional code review.

---

# 18. ACCESSIBILITY STANDARD

Target:

```text
WCAG 2.2 AA
```

All primary flows should support:

```text
keyboard

screen reader semantics

visible focus

sufficient contrast

reduced motion

semantic HTML
```

Do not use color alone to communicate recovery state or anomalies.

---

# 19. TECHNICAL ARCHITECTURE

Overall architecture:

```text
                    WHOOP
                      │
                      │ BLE
                      ▼
               SOMATRIQ MOBILE
                      │
                local SQLite
                      │
                durable queue
                      │
                     HTTPS
                      │
                      ▼

══════════════════ DOKPLOY VPS ══════════════════

                  Dokploy
                  Traefik
                      │
       ┌──────────────┼──────────────┐
       │              │              │
       ▼              ▼              ▼
      WEB            API            MCP
       │              │              │
       └──────────────┼──────────────┘
                      │
            DOMAIN SERVICES
                      │
      ┌───────────────┼────────────────┐
      │               │                │
      ▼               ▼                ▼
 ANALYTICS          AGENT          SCHEDULER
      │               │                │
      └───────────────┼────────────────┘
                      │
                      ▼
              POSTGRESQL
              TIMESCALEDB
              PGVECTOR
                      │
          ┌───────────┴───────────┐
          ▼                       ▼
   STRUCTURED DATA            RAW ARCHIVE

                      │
      ┌───────────────┼────────────────┐
      ▼               ▼                ▼
  TELEGRAM           NTFY          WEB PUSH

                      │
                      ▼
                   BACKUPS
```

---

# 20. DOKPLOY IS THE DEPLOYMENT PLATFORM

The VPS already runs Dokploy.

Somatriq must integrate cleanly with Dokploy.

Do not install another platform abstraction.

Do not add an internal Caddy proxy.

Do not add another Traefik instance.

Dokploy handles ingress and TLS.

---

# 21. DOKPLOY MCP IS REQUIRED

The development agent must use the official Dokploy MCP for infrastructure operations when it is available.

Official package:

```text
@dokploy/mcp
```

Expected environment:

```text
DOKPLOY_URL

DOKPLOY_API_KEY
```

Start with:

```text
DOKPLOY_TOOL_PRESET=deploy
```

because Somatriq normally requires:

```text
project

environment

server

application

compose

domain

deployment
```

Only expand the enabled tool categories when a task requires them.

Do not load the complete hundreds of tool definitions into agent context unnecessarily.

---

# 22. DOKPLOY MCP SAFETY

The agent must distinguish:

```text
READ

REVERSIBLE WRITE

DESTRUCTIVE WRITE
```

Examples of destructive operations:

```text
delete application

delete database

delete volume

remove domain

destroy project

restore database over production

rotate credentials

remove backup
```

Before destructive infrastructure actions:

```text
inspect current state

describe intended change

verify target identifier

ensure backup when appropriate

execute only when authorized
```

Never infer production IDs from names if Dokploy MCP can resolve them first.

---

# 23. DOKPLOY MCP WORKFLOW

Normal deployment workflow:

```text
inspect project

inspect environment

inspect compose configuration

inspect domains

inspect existing deployment

build or update images

deploy

inspect deployment status

inspect logs

run health verification

verify public endpoint

verify private services remain private
```

Use the MCP instead of manually instructing the user to click around Dokploy when the MCP can perform or inspect the operation.

---

# 24. INFRASTRUCTURE AS CODE REMAINS SOURCE OF TRUTH

Dokploy MCP does not replace Git.

Infrastructure configuration that belongs to Somatriq must remain version controlled.

Examples:

```text
compose.yml

Dockerfiles

health checks

environment variable documentation

database initialization

migration behavior

backup scripts

service resource recommendations
```

Dokploy manages deployment state.

Git manages desired application configuration.

---

# 25. DEPLOYMENT ARCHITECTURE

Somatriq Server should be one Dokploy Docker Compose project containing multiple isolated services.

```text
somatriq_web

somatriq_api

somatriq_worker

somatriq_scheduler

somatriq_agent

somatriq_mcp

somatriq_telegram

somatriq_notifications

somatriq_postgres

somatriq_ntfy

somatriq_backup
```

Optional profile:

```text
somatriq_prometheus

somatriq_grafana

somatriq_loki

somatriq_otel
```

---

# 26. DO NOT BUILD A GIANT CONTAINER

One product does not mean one process.

Each service must have a clear responsibility.

The user should still experience deployment as:

```text
one repository

one Compose project

one Dokploy project

one coordinated release
```

---

# 27. NETWORKING

Only expose services that require inbound traffic.

Public:

```text
web

api

mcp

optional ntfy
```

Private:

```text
postgres

worker

scheduler

agent

backup
```

Telegram may require outbound connectivity only when polling.

If webhook mode is later selected, expose only the required webhook endpoint.

---

# 28. DATABASE MUST NEVER BE PUBLIC

Do not publish:

```text
5432
```

to the internet.

PostgreSQL must remain on the private Docker network.

Administrative database access should occur through:

```text
SSH tunnel

Dokploy secure tooling

temporary controlled tunnel
```

not a permanent public port.

---

# 29. REPOSITORY STRUCTURE

Use two main repositories.

## somatriq

Server and web monorepo.

```text
somatriq/

apps/
    web/

services/
    api/
    worker/
    scheduler/
    agent/
    mcp/
    telegram/
    notifications/

packages/
    core/
    contracts/
    db/
    analytics/
    agent_tools/
    connectors/
    observability/

database/
    migrations/
    sql/
    aggregates/
    fixtures/

infra/
    compose/
    postgres/
    backup/
    monitoring/

docs/
    adr/
    architecture/
    domain/
    api/
    database/
    analytics/
    security/
    operations/
    research/
    runbooks/

tests/
    integration/
    e2e/
    fixtures/

prompts/

scripts/

CONTEXT.md

AGENTS.md

compose.yml

.env.example

LICENSE

NOTICE

ATTRIBUTION.md

DISCLAIMER.md
```

## somatriq-mobile

Real fork of NOOP.

Maintain upstream remote.

Do not copy NOOP code into the server repository.

---

# 30. AGENTS.md

Create a root:

```text
AGENTS.md
```

It must summarize mandatory engineering behavior.

At minimum:

```text
Read CONTEXT.md before domain changes.

Read relevant ADRs before architectural changes.

Use TDD for deterministic behavior.

Use grill-with-docs before irreversible design changes.

Use domain-modeling for new domain concepts.

Use research for external protocol and platform assumptions.

Use diagnosing-bugs before speculative bug fixes.

Use code-review before feature completion.

Use design taste skills before and after major UI implementation.

Use Dokploy MCP for Dokploy operations.

Never expose PostgreSQL publicly.

Never give arbitrary SQL to normal MCP clients.

Never let an LLM perform statistical calculations that Python can perform deterministically.

Never modify production destructively without explicit authorization.

Update docs and ADRs with behavior changes.
```

---

# 31. CONTEXT.md

Maintain a living glossary.

Example:

```text
Observation

A source provided measurement associated with a specific time or period.


Derived Metric

A value calculated from one or more observations by a versioned algorithm.


Canonical Metric

The currently selected representation used by Somatriq when multiple sources overlap.


Raw Frame

The original reconstructed BLE protocol frame before interpretation.


Coverage

The proportion of expected data available for a requested interval.


Data Quality

A separate assessment of validity, completeness and consistency.


Insight

A derived human meaningful observation supported by deterministic analysis.


Prediction

A model generated estimate concerning a future outcome.
```

Do not allow different modules to invent conflicting names.

---

# 32. ADR STANDARD

Architecture Decision Records live in:

```text
docs/adr/
```

Naming:

```text
0001-use-postgresql-as-system-of-record.md

0002-preserve-raw-ble-frames.md

0003-use-timescaledb-for-high-frequency-series.md

0004-use-dokploy-for-deployment.md
```

Each ADR:

```text
Context

Decision

Alternatives

Consequences

Status

Date
```

---

# 33. NOOP MOBILE FOUNDATION

Somatriq Mobile is a fork of NOOP.

Reuse its existing strengths:

```text
WHOOP BLE communication

WHOOP 4 support

newer device protocol work where usable

Android UI

local persistence

sleep analytics

HRV

recovery

strain

local operation
```

Somatriq deliberately adds remote synchronization.

Do not try to upstream remote sync unless NOOP's project goals change.

---

# 34. MOBILE ARCHITECTURE

Required flow:

```text
BLE
 ↓
local persistent transaction
 ↓
sync queue
 ↓
background uploader
 ↓
Somatriq Server
```

Never:

```text
BLE
 ↓
remote API
 ↓
local database
```

Remote availability must never be required to preserve wearable data.

---

# 35. MOBILE SYNC MODULE

Create a distinct module or package.

Components:

```text
SyncManager

SyncQueue

SyncWorker

SyncRepository

SyncApiClient

PairingManager

DeviceCredentials

SyncState

SyncDiagnostics
```

Networking code must not leak into BLE protocol classes.

---

# 36. ANDROID BACKGROUND SYNC

Use WorkManager or the appropriate current Android background execution mechanism.

Sync should be:

```text
battery conscious

retryable

offline tolerant

idempotent

observable

cancel safe
```

---

# 37. SYNC INTERVAL

Default target:

```text
5 minutes
```

Do not send one HTTP request per heart rate event.

Batch.

Important events may request earlier sync:

```text
completed sleep

completed workout

manual force sync
```

---

# 38. MOBILE LOCAL STORAGE

SQLite remains functional.

Somatriq Server is the long term source of truth.

The phone is an offline capable cache and capture system.

---

# 39. DURABLE SYNC QUEUE

A record remains pending until acknowledged by the server.

States:

```text
pending

uploading

acknowledged

failed_retryable

failed_permanent
```

Retryable errors must use exponential backoff with jitter.

---

# 40. IDEMPOTENCY

The server must safely accept the same data multiple times.

Use:

```text
batch UUID

idempotency key

content hash

source record identifiers
```

Network retries must not create duplicate physiological observations.

---

# 41. INGEST API

Primary endpoint:

```text
POST /api/v1/ingest/batches
```

Support compressed payloads.

Preferred:

```text
zstd
```

Allow gzip fallback when appropriate.

---

# 42. INGEST RESPONSE

Return structured acknowledgement.

Example:

```json
{
  "batch_id": "uuid",
  "accepted": true,
  "records_received": 1248,
  "records_inserted": 1246,
  "records_duplicate": 2,
  "warnings": [],
  "server_time": "ISO timestamp"
}
```

The mobile application marks a batch synchronized only after valid acknowledgement.

---

# 43. MOBILE PAIRING

Web creates a short lived pairing session.

User can:

```text
scan QR

or

enter pairing code
```

The server issues device specific credentials.

Credentials must have minimal scopes.

---

# 44. DEVICE SCOPES

Mobile credential:

```text
ingest.write

device.read

sync.read
```

It must not allow:

```text
admin

arbitrary health reads

other user access

database access
```

---

# 45. SECRET STORAGE

Android secrets must use the Android secure key mechanism.

Server must store token hashes where practical.

No credentials in logs.

No credentials in crash reports.

---

# 46. THREE DATA LEVELS

Somatriq must distinguish:

```text
RAW

OBSERVATION

DERIVED
```

## RAW

Original protocol evidence.

## OBSERVATION

Decoded measurement.

Examples:

```text
heart rate

RR interval

SpO2

temperature

respiration

battery
```

## DERIVED

Calculated interpretation.

Examples:

```text
HRV

RHR

sleep score

recovery

strain

training load

baseline deviation
```

Never collapse these layers.

---

# 47. RAW PRESERVATION

Where technically possible, preserve complete reconstructed BLE frames before interpretation.

Flow:

```text
BLE fragments
 ↓
complete frame
 ├────→ raw archive
 │
 └────→ decoder
          ↓
       observation
```

This allows future decoder improvements.

---

# 48. RAW BLOB STORE

Large original payloads should not live directly as PostgreSQL blobs by default.

Define abstraction:

```text
RawBlobStore
```

Default implementation:

```text
LocalVolumeRawBlobStore
```

Future:

```text
S3RawBlobStore
```

---

# 49. RAW STORAGE FORMAT

Compressed immutable objects.

Suggested:

```text
/raw/
  year/
    month/
    day/
      user/
        device/
          batch_uuid.zst
```

PostgreSQL stores:

```text
path

hash

size

compression

schema version

device

time range

record count
```

---

# 50. RAW RETENTION

Configurable:

```text
forever

365 days

180 days

90 days

disabled
```

Default for a personal VPS installation:

```text
forever
```

assuming adequate storage.

---

# 51. MOBILE RAW RETENTION

After confirmed server upload:

```text
retain 7 days
```

then prune local raw copies.

If server synchronization is disabled, do not apply this server based retention policy.

---

# 52. SERVER DATABASE

Use:

```text
PostgreSQL

TimescaleDB

pgvector
```

PostgreSQL is the primary system of record.

Avoid adding databases without clear need.

Do not introduce by default:

```text
MongoDB

InfluxDB

Qdrant

Pinecone

Elasticsearch
```

---

# 53. POSTGRES IMAGE

Build and pin a controlled image with the required extensions.

Never rely blindly on `latest`.

Record exact versions in:

```text
infra/postgres/
```

---

# 54. DATABASE SCHEMAS

Use explicit PostgreSQL schemas:

```text
identity

ingest

raw

timeseries

health

derived

research

ai

notifications

audit

system
```

---

# 55. IDENTITY SCHEMA

Tables:

```text
users

devices

data_sources

api_clients

tokens

permissions

user_preferences
```

Design IDs to permit multiple users in the future even if initial deployment is one person.

Do not implement unnecessary multi tenant complexity.

---

# 56. INGEST SCHEMA

Tables:

```text
batches

events

failures

sync_state

idempotency_keys
```

A batch should contain metadata sufficient for replay and forensic debugging.

---

# 57. TIMESERIES SCHEMA

Hypertables:

```text
heart_rate

rr_intervals

spo2

skin_temperature

respiration

battery

motion_features
```

Do not retain extremely high frequency IMU indefinitely in relational rows by default.

Keep original data in raw storage and materialize useful features.

---

# 58. HEALTH SCHEMA

Tables:

```text
sleep_sessions

sleep_stages

workouts

exercises

exercise_sets

daily_activity

nutrition

hydration

caffeine

body_measurements

journal_entries

context_events
```

---

# 59. DERIVED SCHEMA

Tables:

```text
daily_metrics

hourly_metrics

daily_features

recovery

strain

stress

sleep_scores

training_load

baselines

anomalies

features

algorithm_runs
```

---

# 60. RESEARCH SCHEMA

Tables:

```text
experiments

experiment_phases

interventions

checkins

outcomes

analysis_runs

predictions

model_runs
```

---

# 61. AI SCHEMA

Tables:

```text
conversations

messages

memory

insights

recommendations

feedback

model_usage

prompt_runs
```

---

# 62. PROVENANCE IS MANDATORY

Every derived metric needs traceability.

Store:

```text
metric name

value

unit

time range

source observations

source device

source provider

algorithm name

algorithm version

algorithm parameters

code commit when relevant

quality

calculated_at
```

---

# 63. ALGORITHM VERSIONING

Never silently overwrite historical calculation semantics.

Examples:

```text
noop_recovery_v1

somatriq_recovery_v1

somatriq_recovery_v2
```

Allow parallel comparison.

---

# 64. ALGORITHM REGISTRY

Create:

```text
system.algorithms
```

Fields:

```text
name

version

description

parameters

code_commit

created_at

deprecated_at
```

---

# 65. TIME HANDLING

Store timestamps in UTC using timezone aware PostgreSQL fields.

Store source timezone when semantically relevant.

Daily calculations must respect user timezone.

Explicitly test daylight saving transitions.

Sleep spanning midnight must not be broken by simplistic date grouping.

---

# 66. CANONICAL DATA MODEL

Multiple sources may provide the same concept.

Example:

```text
WHOOP heart rate

Huawei heart rate

Health Connect heart rate
```

Store each source.

Then resolve a canonical representation.

Never discard originals.

---

# 67. SOURCE PRIORITY

Example configuration:

```text
HRV
WHOOP

Sleep
WHOOP

Steps
Huawei

GPS
Huawei

Continuous HR
WHOOP

Weight
smart scale
```

User should be able to override.

---

# 68. SOURCE RESOLVER

Create a deterministic service:

```text
SourceResolver
```

Inputs:

```text
metric

source priority

coverage

quality

overlap

user preference
```

Output:

```text
canonical source
```

---

# 69. DATA QUALITY ENGINE

This is a first class product capability.

Detect:

```text
coverage

missing windows

duplicates

overlap

invalid values

clock drift

decoder failures

sync delay

source disagreement

unexpected sampling changes
```

---

# 70. QUALITY AND CONFIDENCE ARE DIFFERENT

Do not conflate:

```text
Data Quality
```

with:

```text
Statistical Confidence
```

A regression can have statistical confidence but use poor source data.

Represent both.

---

# 71. CONTINUOUS AGGREGATES

Use TimescaleDB aggregation for high frequency series.

Example:

```text
raw HR
 ↓
1 minute
 ↓
5 minute
 ↓
1 hour
 ↓
1 day
```

Create similar aggregation strategies for suitable metrics.

The web must not request millions of raw samples to render a one year chart.

---

# 72. DAILY FEATURE STORE

Create one coherent daily feature representation.

Example fields:

```text
date

sleep_duration_minutes

time_in_bed_minutes

sleep_efficiency

deep_sleep_minutes

rem_sleep_minutes

light_sleep_minutes

awake_minutes

sleep_onset

wake_time

bedtime_variability

hrv_rmssd

resting_hr

average_sleep_hr

minimum_sleep_hr

respiratory_rate

spo2_mean

spo2_min

skin_temperature

temperature_delta

steps

active_minutes

calories_estimated

cardio_load

muscular_load

systemic_load

workout_minutes

caffeine_mg

last_caffeine_time

protein_g

calories_consumed

weight_kg

body_fat_percent

subjective_energy

subjective_stress

recovery_score

sleep_score

strain_score

data_quality
```

This is heavily used by analytics.

---

# 73. ANALYTICS STACK

Use Python.

Recommended:

```text
NumPy

Pandas

SciPy

statsmodels

scikit learn
```

LLMs must never substitute these calculations.

---

# 74. BASELINE ENGINE

Calculate personal baselines for:

```text
HRV

RHR

sleep

respiration

temperature

training load

weight
```

Support:

```text
7 days

28 days

90 days
```

Use robust statistical methods where appropriate.

Evaluate:

```text
median

MAD

EWMA

percentiles
```

rather than relying exclusively on arithmetic means.

---

# 75. HRV ENGINE

Primary:

```text
RMSSD
```

Where valid:

```text
SDNN

pNN50

mean RR
```

Keep:

```text
samples

valid samples

artifact count

coverage

filter version
```

---

# 76. RECOVERY ENGINE

Recovery must be explainable.

Example:

```text
Recovery 78

HRV contribution
positive

RHR contribution
positive

Sleep contribution
positive

Temperature contribution
neutral

Training load contribution
negative
```

The exact formula is versioned.

---

# 77. SLEEP ENGINE

Initially Somatriq can preserve NOOP results.

Store separately:

```text
source sleep

noop derived sleep

Somatriq derived sleep
```

Future server side calculations must not erase previous interpretations.

---

# 78. TRAINING MODEL

Separate:

```text
Cardiovascular Load

Muscular Load

Systemic Load
```

This matters especially for strength training.

---

# 79. STRENGTH DATA

Capture:

```text
exercise

muscle groups

sets

repetitions

weight

RIR

RPE

duration
```

Calculate:

```text
tonnage

hard sets

relative intensity

estimated 1RM

volume by muscle group
```

---

# 80. PERSONAL RESPONSE MODEL

Over time analyze:

```text
training session
 ↓
HRV next day

RHR next day

sleep

recovery

subjective state
```

Enable questions such as:

```text
How do heavy leg sessions affect my next day HRV?
```

---

# 81. CORRELATION ENGINE

Support:

```text
Pearson

Spearman

partial correlation

linear regression

multiple regression

robust regression
```

Every result returns:

```text
N

effect size

confidence interval

method

missing observations

controlled variables

data quality

limitations
```

Never display only a correlation coefficient.

---

# 82. CAUSAL LANGUAGE

UI and AI must distinguish:

```text
association

prediction

experimental effect

causality
```

Do not say:

```text
X caused Y
```

from observational correlations.

---

# 83. EXPERIMENT ENGINE

Support personal N of 1 experiments.

Core entities:

```text
experiment

hypothesis

phase

intervention

checkin

outcome

analysis
```

Initial designs:

```text
baseline then intervention

AB

ABAB

custom
```

---

# 84. EXPERIMENT EXAMPLE

```text
Question

Does avoiding caffeine after 14:00 improve my sleep?


Baseline

14 days


Intervention

21 days


Primary outcomes

HRV
sleep efficiency


Secondary outcomes

RHR
sleep duration
deep sleep
REM
```

---

# 85. COMPLIANCE

Never assume intervention adherence.

Record:

```text
yes

no

unknown
```

Telegram and web can provide rapid checkins.

---

# 86. EXPERIMENT RESULTS

Return:

```text
baseline N

intervention N

compliance

mean difference

median difference

effect size

confidence interval

data quality

potential confounders
```

---

# 87. PREDICTIONS

Possible future models:

```text
tomorrow recovery

expected HRV

sleep need

training readiness

sleep debt trajectory
```

Each prediction requires:

```text
model name

model version

features version

prediction

uncertainty

generated time

target time

actual result later

prediction error
```

---

# 88. MODEL EVALUATION

Compare models objectively.

Use:

```text
MAE

RMSE

calibration

coverage
```

Do not deploy a complicated model if a simple baseline performs better.

---

# 89. AI ROLE

The LLM:

```text
understands question

selects tools

requests deterministic computation

interprets structured result

communicates result
```

The LLM does not:

```text
invent missing health data

calculate statistics from huge arrays manually

bypass permissions

write raw SQL

diagnose disease
```

---

# 90. AGENT ENGINE

Use a provider neutral orchestration layer.

LangGraph is acceptable.

Start with one orchestrator plus good tools.

Do not prematurely create fifteen agents.

Potential future specialists:

```text
Sleep

Training

Nutrition

Experiments

Statistics

Research
```

---

# 91. LLM PROVIDER ABSTRACTION

Create:

```text
LLMProvider
```

Adapters:

```text
OpenAI

Anthropic

OpenAI compatible

Ollama
```

No core service should import a vendor SDK directly outside the adapter layer unless justified.

---

# 92. AI PRIVACY LEVELS

Configurable:

```text
summary_only

aggregates

detailed
```

Default:

```text
aggregates
```

Do not send raw continuous sensor streams to external LLMs.

---

# 93. AI MEMORY

Separate textual memory from health facts.

Memory categories:

```text
profile

goal

preference

constraint

commitment

observation

experiment
```

Use pgvector only for appropriate textual semantic retrieval.

Do not embed millions of physiological rows.

---

# 94. DETERMINISTIC AI TOOLS

Examples:

```text
get_today

get_metric_series

get_sleep

get_hrv

get_rhr

get_recovery

get_training_load

compare_periods

calculate_correlation

run_regression

get_baselines

get_anomalies

get_experiment

get_data_quality
```

---

# 95. MCP SERVER

Somatriq provides its own health MCP server.

Do not expose PostgreSQL as the normal MCP.

Architecture:

```text
AI client
 ↓
Somatriq MCP
 ↓
domain service
 ↓
analytics
 ↓
PostgreSQL
```

---

# 96. MCP SERVER TECHNOLOGY

Use:

```text
FastMCP
```

or the current stable equivalent if project research determines a better official mechanism.

Expose over authenticated HTTPS.

---

# 97. MCP DEFAULT PERMISSION

Default:

```text
read only
```

Scopes:

```text
health.read

journal.write

experiment.write

memory.read

memory.write

admin
```

Normal desktop AI connection receives only:

```text
health.read
```

unless user explicitly grants more.

---

# 98. MCP TOOLS

Initial tools:

```text
health_today

health_metric_series

health_sleep

health_sleep_detail

health_hrv

health_rhr

health_recovery

health_training

health_body

health_nutrition

health_compare_periods

health_correlations

health_anomalies

health_baselines

health_data_quality

health_sources

health_experiments

health_experiment_result
```

Write tools:

```text
journal_add

experiment_create

experiment_checkin
```

---

# 99. MCP RESPONSE CONTRACT

Every analysis response includes:

```json
{
  "data": {},
  "coverage": {},
  "sources": [],
  "quality": 0.97,
  "caveats": [],
  "generated_at": "timestamp"
}
```

This is essential for grounded AI interpretation.

---

# 100. NO GENERAL SQL MCP

Do not provide normal clients:

```text
execute_sql
```

If an administrative query tool exists:

```text
admin only

disabled by default

read only by default

fully audited
```

---

# 101. TELEGRAM

Telegram is a primary conversational surface.

Features:

```text
morning brief

health questions

journal

caffeine logging

training logging

experiment checkins

reminders

anomaly alerts
```

---

# 102. TELEGRAM MORNING BRIEF

Example:

```text
Good morning

Recovery
78

Sleep
7 h 21 min

HRV
54 ms
6 percent above baseline

RHR
56 bpm

Today

Training
Normal

Sleep target
7 h 35 min

Main insight

Sleep duration increased substantially compared
with your recent average and HRV also improved.

Data quality
97 percent
```

---

# 103. NATURAL LANGUAGE LOGGING

Example:

```text
I drank a coffee now
```

Structured output:

```text
event
caffeine

timestamp
current time

quantity
unknown
```

Do not invent quantity.

---

# 104. TRAINING PARSER

Example:

```text
Chest press 180x8 170x9 160x10
```

Store structured sets.

Parsing can use AI.

Final validated representation must be deterministic and editable.

---

# 105. NOTIFICATION ENGINE

Channels:

```text
Telegram

ntfy

Web Push

email optional

webhook
```

Categories:

```text
routine

insight

experiment

anomaly

system
```

---

# 106. ANTI NOTIFICATION FATIGUE

Implement:

```text
quiet hours

cooldowns

deduplication

daily limits

priority

acknowledgement
```

No repeated alarm because one underlying condition was recalculated several times.

---

# 107. WEB APPLICATION

The web application is the primary rich interface.

Recommended stack:

```text
TypeScript

React

Next.js

TanStack Query

ECharts
```

The agent may propose alternatives through an ADR if objectively superior.

---

# 108. TYPESCRIPT STANDARDS

Enable strict type checking.

Do not use:

```text
any
```

without explicit isolated justification.

Prefer:

```text
unknown
```

at untrusted boundaries followed by validation.

Use generated or shared API contracts.

Do not duplicate health type definitions independently across frontend modules.

---

# 109. PYTHON STANDARDS

Use modern type hints throughout application code.

Required:

```text
Ruff

type checker

pytest
```

Select one type checker project wide.

Do not alternate between competing systems arbitrarily.

Public functions in core domain packages require typed inputs and outputs.

---

# 110. KOTLIN STANDARDS

Follow Kotlin official conventions.

Favor:

```text
immutable state

coroutines

structured concurrency

Flow where appropriate

explicit repository boundaries
```

Avoid GlobalScope.

Network activity must not run on the UI thread.

---

# 111. API CONTRACTS

FastAPI exposes OpenAPI.

Frontend client generation should use the OpenAPI source rather than manually duplicating every schema.

MCP contracts remain explicit and stable.

---

# 111. API CONTRACTS

FastAPI exposes OpenAPI.

Frontend client generation should use the OpenAPI source rather than manually duplicating every schema.

MCP contracts remain explicit and stable.

---

# 112. VALIDATION

All untrusted boundaries require schema validation:

```text
mobile payloads

API requests

Telegram parsed actions

MCP arguments

imported files

external connector payloads
```

---

# 113. FRONTEND NAVIGATION

Primary sections:

```text
Today

Sleep

Recovery

Training

Activity

Body

Nutrition

Trends

Explore

Correlations

Experiments

Journal

Coach

Data Sources

Data Quality

Settings

Admin
```

---

# 114. TODAY SCREEN

Answer:

```text
How am I?

What changed?

What matters today?
```

Show:

```text
Recovery

Sleep

HRV

RHR

Training state

Sleep target

Main insight

Important anomaly

Current experiment
```

Avoid twenty equal priority cards.

---

# 115. SCORE EXPLAINABILITY

Any score has:

```text
Why?
```

Open explanation containing contributors and algorithms.

A user must be able to inspect:

```text
source

algorithm

algorithm version

quality
```

when entering advanced detail.

---

# 116. EXPLORE

Allow:

```text
metric selection

time range

aggregation

source

algorithm version

overlay metric
```

Ranges:

```text
24h

7d

30d

90d

1y

all

custom
```

---

# 117. CORRELATIONS PAGE

User chooses target.

Example:

```text
What seems associated with my HRV?
```

Return ranked results with scientific context.

Each can open:

```text
scatter plot

sample

model

controlled variables

confidence interval

missing data

limitations
```

---

# 118. EXPERIMENTS PAGE

Wizard:

```text
Question

Hypothesis

Baseline

Intervention

Outcome metrics

Duration

Checkin schedule
```

Show progress and compliance.

---

# 119. DATA QUALITY PAGE

Show:

```text
coverage per metric

coverage per source

missing periods

duplicate rates

sync delay

source conflicts

decoder errors

latest successful sync
```

---

# 120. ADMIN PAGE

Show:

```text
API

database

worker

scheduler

agent

MCP

Telegram

ntfy

storage

raw archive

database size

pending jobs

failed jobs

last backup

backup verification

mobile last sync

server version

mobile version
```

---

# 121. PWA

The web should be installable.

Support:

```text
responsive mobile layout

desktop layout

web push

offline shell where useful
```

Do not move BLE capture into the PWA.

---

# 122. AUTHENTICATION

Initial local account.

Passwords hashed with a modern memory hard algorithm such as Argon2id.

Add later:

```text
TOTP

WebAuthn
```

Sessions:

```text
secure cookies

HttpOnly

SameSite

CSRF protection where relevant
```

---

# 123. PERSONAL ACCESS TOKENS

Each token has:

```text
name

scopes

created

last used

expiry

revoked
```

Show raw token only once.

Store hash.

---

# 124. AUDIT LOG

Record security sensitive operations:

```text
login

failed login

token creation

token revocation

MCP writes

journal writes

experiment changes

data export

settings

backup restore

admin operation

Dokploy triggered deployment where available
```

No secrets.

---

# 125. HEALTH SAFETY LANGUAGE

Somatriq is not a medical device.

Never phrase analytics as diagnosis.

Allowed:

```text
Your resting heart rate is substantially above your recent baseline.
```

Not allowed:

```text
You have condition X.
```

When appropriate:

```text
Persistent or concerning changes may warrant professional evaluation.
```

---

# 126. CONNECTOR ARCHITECTURE

Interface:

```text
Connector

authenticate

discover

backfill

incremental_sync

normalize

health_check
```

Planned:

```text
NOOP

Health Connect

Huawei

Apple Health

Withings

Garmin

Oura

CSV

manual
```

---

# 127. HEALTH CONNECT

Implement as a connector rather than coupling Health Connect semantics directly to core tables.

Normalize to canonical observations.

Preserve source payload metadata.

---

# 127. HEALTH CONNECT

Implement as a connector rather than coupling Health Connect semantics directly to core tables.

Normalize to canonical observations.

Preserve source payload metadata.

---

# 128. HUAWEI

Huawei integration must not contaminate WHOOP specific code.

Create a separate connector.

If access is indirect:

```text
Huawei
 ↓
bridge or export
 ↓
Somatriq connector
 ↓
canonical model
```

---

# 129. EXTERNAL DATA IMPORT

Support:

```text
CSV

JSON

Parquet where appropriate

NOOP export

Somatriq export
```

Import preview must show:

```text
date range

metrics

record count

duplicates

validation warnings
```

before commit.

---

# 130. DATA EXPORT

Users can export everything.

Structured:

```text
JSON

CSV

Parquet
```

Also allow raw archived objects.

No lock in.

---

# 131. DELETE

Support:

```text
delete source

delete time range

delete journal

delete memory

delete experiments

delete all user data
```

Deletion of raw data must be consistent with deletion request.

---

# 132. API IMPLEMENTATION

Recommended backend:

```text
FastAPI

Pydantic

SQLAlchemy

Alembic

asyncpg
```

Keep API schemas separate from ORM representations.

---

# 133. PYTHON PACKAGE MANAGEMENT

Use:

```text
uv
```

or equivalent modern locked dependency workflow.

A lock file is mandatory.

No unpinned production dependencies.

---

# 134. DATABASE MIGRATIONS

Use Alembic.

Every schema change must have a migration.

CI must test migration from previous supported schema.

Do not mutate schema implicitly during API startup.

---

# 135. BACKGROUND JOBS

Use a durable PostgreSQL backed job system initially.

Jobs:

```text
process ingest batch

decode raw batch

calculate HRV

calculate sleep

refresh daily features

calculate baselines

calculate recovery

calculate training load

detect anomalies

update experiments

calculate correlations

generate morning brief

send notification

generate prediction

run retention

verify backups
```

---

# 136. JOB CONTRACT

Each job contains:

```text
id

type

payload

status

attempts

max attempts

scheduled time

started

finished

error
```

Jobs must be retry safe.

---

# 136. JOB CONTRACT

Each job contains:

```text
id

type

payload

status

attempts

max attempts

scheduled time

started

finished

error
```

Jobs must be retry safe.

---

# 137. SCHEDULER

Scheduler creates jobs.

It does not perform heavy work itself.

Do not run recurring analytics inside the API process.

---

# 138. LATE ARRIVING DATA

Late observations must invalidate only affected derived periods.

Example:

```text
late sleep observation
 ↓
invalidate affected sleep
 ↓
daily feature
 ↓
recovery
 ↓
experiment result if affected
```

Do not blindly recompute all historical data.

---

# 139. DEPENDENCY INVALIDATION

Implement an explicit dependency graph or equivalent mechanism.

Derived values should know which inputs or periods can invalidate them.

---

# 140. REPROCESSING

Provide administrative commands for:

```text
user

date range

metric

source

algorithm version
```

Example conceptual:

```text
somatriq reprocess hrv
```

Manual SQL should not be required for normal recalculation.

---

# 141. MORNING INTELLIGENCE PIPELINE

Sequence:

```text
sync assessment

data quality

sleep processing

HRV processing

daily features

baseline comparison

recovery

training load

anomaly detection

experiment update

insight candidate generation

insight ranking

LLM explanation

notification
```

The LLM appears near the end.

---

# 142. INSIGHT ENGINE

Generate deterministic candidate insights.

Examples:

```text
HRV materially above baseline

lowest sleep duration in 14 days

RHR elevated

training load unusually high

bedtime consistency improving

experiment compliance low
```

Rank by:

```text
importance

novelty

confidence

actionability
```

---

# 143. AI TRACEABILITY

Store for each AI generated insight:

```text
provider

model

prompt version

tools called

time period

input metric references

generated timestamp

token usage

cost estimate
```

Do not store hidden chain of thought.

---

# 144. PROMPTS

Version prompts in Git.

Directory:

```text
prompts/
```

A critical prompt should not live only inside Dokploy environment variables.

---

# 145. AI COST

Track:

```text
provider

model

feature

input tokens

output tokens

estimated cost
```

Expose optional usage view.

---

# 146. OBSERVABILITY

Services expose:

```text
/health

/ready

/metrics
```

where appropriate.

Docker health checks are mandatory.

---

# 147. LOGGING

Structured logs.

Fields:

```text
timestamp

level

service

request_id

job_id

device_id when useful

event

duration

error_code
```

Never log:

```text
password

API key

session token

MCP token

device secret
```

---

# 148. OPENTELEMETRY

Instrument:

```text
API

worker

agent

MCP
```

Support optional OpenTelemetry deployment.

---

# 149. PRODUCT METRICS

Measure:

```text
ingest batches

records ingested

duplicates prevented

ingest latency

mobile sync age

failed jobs

job duration

database latency

AI latency

notification errors

raw storage growth

database growth
```

---

# 150. GRAFANA

Optional.

Grafana is infrastructure observability.

It is not the Somatriq user dashboard.

---

# 151. CI

Required CI stages:

```text
format

lint

typecheck

unit tests

integration tests

migration tests

contract tests

frontend build

backend build

mobile relevant tests

Docker build

Compose validation

dependency vulnerability scan

secret scan

SBOM generation
```

---

# 152. E2E

At least one complete end to end path:

```text
synthetic wearable batch
 ↓
API ingest
 ↓
PostgreSQL
 ↓
analytics
 ↓
Today API
 ↓
web
```

Another:

```text
database fixture
 ↓
MCP tool
 ↓
structured response
```

---

# 153. GOLDEN DATASETS

Create test fixtures:

```text
normal day

missing RR

duplicate batch

out of order records

clock drift

late data

two overlapping sources

sleep over DST

corrupt frame

partial sync

server unavailable

replayed batch
```

---

# 154. COVERAGE PHILOSOPHY

Do not chase a meaningless global percentage.

Critical deterministic domain behavior should have near complete meaningful coverage.

Focus especially on:

```text
ingest

auth

sync

analytics

experiments

source resolution

data quality
```

Frontend visual code does not need artificial tests for every markup line.

---

# 155. MUTATION TESTING

Consider mutation testing for critical calculation packages once basic tests mature.

Especially:

```text
HRV

recovery

source resolver

experiment statistics
```

Use it to evaluate test strength, not as a vanity metric.

---

# 156. CODE QUALITY

Prefer deep modules with small interfaces.

Avoid wrappers that add no abstraction.

Avoid generic helper directories that become dumping grounds.

Do not introduce an interface with only one trivial implementation unless a real boundary exists.

Good abstractions should hide complexity.

---

# 157. ERROR MODEL

Define explicit error classes.

Examples:

```text
ValidationError

AuthenticationError

AuthorizationError

DataQualityError

ConnectorError

RetryableIngestError

PermanentIngestError

AlgorithmError
```

API errors must have stable machine readable codes.

---

# 158. NO SILENT FAILURE

Any background failure that can lead to missing health data must become visible.

Surface via:

```text
Admin

Data Quality

logs

system notification when significant
```

---

# 159. SECURITY REVIEW

Before each major release evaluate:

```text
authentication

authorization

token leakage

public services

database exposure

SSRF

SQL injection

XSS

CSRF

file import

archive extraction

MCP permissions

agent prompt injection

backup exposure

dependency risks
```

---

# 160. MCP PROMPT INJECTION DEFENSE

Health data and journals are untrusted content.

Do not treat text from imported data as system instructions.

Tools must have fixed permissions enforced server side.

The model cannot expand its own scopes.

---

# 161. DEPENDENCY POLICY

Prefer mature dependencies.

Every dependency should answer:

```text
What problem does it solve?

Could standard library solve it?

Is it maintained?

What is its license?

Does it create lock in?

Does it run in critical path?
```

Avoid adding packages for one trivial utility.

---

# 162. LICENSE AUDIT

Maintain:

```text
THIRD_PARTY_LICENSES.md
```

Automate dependency license reporting when feasible.

NOOP attribution must remain intact.

Never copy code from an unclear license repository.

Architecture ideas may be reimplemented.

---

# 163. SECURITY SCANNING

CI should include suitable scanners for:

```text
dependencies

containers

secrets

SBOM
```

Do not automatically apply destructive dependency upgrades without tests.

---

# 164. VERSIONING

Somatriq Server uses semantic versions.

Mobile maintains its own version plus upstream NOOP lineage.

Every ingestion request declares protocol schema version.

---

# 165. API VERSION

Use:

```text
/api/v1/
```

Breaking changes require explicit migration strategy.

Do not casually create `v2` because a field was added.

---

# 166. DATABASE BACKUP

Use two complementary layers.

## Somatriq backup service

PostgreSQL logical backups plus raw archive backups.

## Dokploy backup facilities

Use Dokploy volume backups or database backups where appropriate.

---

# 167. NAMED VOLUMES

Persistent data must use named volumes suitable for Dokploy backup management.

Examples:

```text
somatriq_postgres_data

somatriq_raw_data

somatriq_ntfy_data

somatriq_backup_data
```

---

# 168. EXTERNAL BACKUP DESTINATION

Support:

```text
S3 compatible storage

Backblaze B2

MinIO

SFTP if implemented safely
```

Encrypt backups.

---

# 169. RETENTION

Suggested:

```text
14 daily

8 weekly

12 monthly
```

Configurable.

---

# 170. BACKUP VERIFICATION

A backup existing does not prove it can be restored.

Regularly verify:

```text
object exists

checksum valid

database archive readable
```

Prefer occasional restore into temporary isolated database.

---

# 171. RESTORE RUNBOOK

Maintain:

```text
docs/runbooks/restore.md
```

It must explain how to recover:

```text
PostgreSQL

raw archive

configuration

secrets

Dokploy deployment
```

---

# 172. DISASTER RECOVERY GOAL

A new VPS should be able to recover Somatriq from:

```text
Git repository

database backup

raw archive backup

secret backup
```

No critical configuration should exist solely as tribal knowledge.

---

# 173. WEB PERFORMANCE

Set explicit budgets.

Examples:

```text
Today page should not download enormous datasets.

Charts must request aggregated data appropriate to viewport.

Lazy load advanced analytics.

Avoid shipping large visualization libraries to pages that do not use them.
```

Measure actual performance.

---

# 174. CHART DESIGN

Charts must support:

```text
tooltips

units

source indication

time zone correctness

missing data gaps

quality indication

responsive resizing

accessible labels where practical
```

Never interpolate missing measurements visually without indicating interpolation.

---

# 175. DESIGN SYSTEM

Create explicit tokens.

```text
typography

spacing

radius

elevation

motion

color

semantic health states

chart palette
```

The design taste skill should help define them.

Document in:

```text
docs/design/
```

or equivalent.

---

# 176. HEALTH COLORS

Do not create simplistic:

```text
green good

red bad
```

for everything.

Recovery should communicate context without alarming the user.

Anomalies need visual distinction but not medical panic.

---

# 177. MOTION

Motion should communicate:

```text
state change

navigation hierarchy

data update

feedback
```

Do not animate charts gratuitously every time the page loads.

Do not animate charts gratuitously every time the page loads.

Respect reduced motion.

---

# 178. EMPTY STATES

Examples:

```text
No complete sleep yet

Baseline still collecting

No experiments

No nutrition source

No training sessions
```

Explain the next useful action.

---

# 179. BASELINE WARMUP

States:

```text
collecting

early

established
```

Do not show false precision after two nights.

Example:

```text
Baseline

8 of 14 recommended nights collected
```

---

# 180. FIRST RUN EXPERIENCE

Setup wizard:

```text
Create admin

Select timezone

Configure backup

Configure AI optional

Configure Telegram optional

Pair mobile

Receive first measurement
```

Visible milestones:

```text
First sample

First complete night

First stable baseline

First recovery

First weekly insight
```

---

# 181. API ENDPOINTS

Initial groups:

```text
/api/v1/auth

/api/v1/devices

/api/v1/ingest

/api/v1/today

/api/v1/metrics

/api/v1/sleep

/api/v1/recovery

/api/v1/training

/api/v1/activity

/api/v1/body

/api/v1/nutrition

/api/v1/journal

/api/v1/correlations

/api/v1/experiments

/api/v1/insights

/api/v1/data-quality

/api/v1/sources

/api/v1/admin
```

---

# 182. REALTIME

Use WebSocket only where it adds actual value.

Possible:

```text
live heart rate

sync status

long AI response streaming

job completion
```

Normal dashboards can use efficient HTTP query caching.

---

# 183. LIVE HEART RATE

Realtime display should be separate from persistence.

```text
mobile
 ↓
live stream endpoint
 ↓
websocket
 ↓
web
```

Historical persistence still follows normal ingest.

Avoid duplicate storage from both paths.

---

# 184. CACHING

Do not introduce Redis immediately.

Use:

```text
Timescale aggregates

PostgreSQL

HTTP caching

application memory cache when safe
```

Add Redis only after evidence supports the need.

---

# 185. SEARCH

Structured health queries should remain structured.

Text search:

```text
journal

memory

insights
```

can use:

```text
PostgreSQL full text

pgvector
```

Do not turn numerical health analysis into vector search.

---

# 186. ASK SOMATRIQ

Example user question:

```text
Why has my HRV been lower this week?
```

Agent flow:

```text
get HRV

get sleep

get training

get anomalies

check quality

compare periods

request statistics if needed

explain result
```

---

# 187. EVIDENCE LABELS IN AI ANSWERS

Distinguish:

```text
Measured

Calculated

Associated

Predicted

AI interpretation
```

This should be visible in advanced mode.

---

# 188. REPO WORKFLOW

Prefer small coherent commits.

Each commit should leave repository usable when practical.

Do not produce a week of unreviewable generated code in one commit.

---

# 189. COMMIT STYLE

Use a consistent conventional format.

Example:

```text
feat(ingest): add idempotent batch endpoint

test(sync): cover replayed mobile batches

fix(hrv): reject invalid RR artifacts

docs(adr): record raw archive decision
```

---

# 190. PULL REQUEST STANDARD

A PR must explain:

```text
Problem

Solution

Architecture impact

Data migration

Security impact

Testing

Screenshots for UI

Observability impact

Rollback
```

Include which relevant skills were used when useful.

---

# 191. DOCUMENTATION IS PART OF DONE

Feature completion includes relevant:

```text
README

OpenAPI

ADR

CONTEXT

runbook

migration notes

configuration docs
```

depending on feature.

---

# 192. DEFINITION OF DONE

A backend feature is not done until:

```text
behavior exists

tests pass

types pass

lint passes

failure paths exist

logs exist

metrics exist when relevant

security reviewed

migration exists when needed

docs updated

API documented
```

A frontend feature additionally requires:

```text
responsive behavior

accessibility

loading

empty

error

design review

browser E2E for critical flow
```

---

# 193. INITIAL MILESTONES

## M0

Repository bootstrap.

Create:

```text
AGENTS.md

CONTEXT.md

ADR directory

CI

Compose

PostgreSQL

API skeleton

Web skeleton
```

Validate Dokploy MCP connection.

Deploy hello health endpoints through Dokploy MCP.

---

# 194. M1

Vertical tracer bullet:

```text
synthetic HR observation
 ↓
ingest API
 ↓
PostgreSQL
 ↓
GET metric
 ↓
web chart
```

TDD.

Deploy through Dokploy MCP.

---

# 195. M2

NOOP mobile sync.

```text
WHOOP
 ↓
NOOP fork
 ↓
SQLite
 ↓
sync queue
 ↓
API
 ↓
Postgres
```

Acceptance:

A real WHOOP heart rate sample reaches server automatically.

---

# 196. M3

Offline reliability.

Test:

```text
disable VPS

collect data

restore VPS

sync

verify no loss

verify no duplicates
```

This is a hard release gate.

---

# 197. M4

Raw archive.

Preserve protocol frames.

Verify replay.

Create reprocessing tooling.

---

# 198. M5

Core health.

```text
HRV

RHR

sleep

daily features

data quality

baseline
```

---

# 199. M6

Recovery and Today.

Use design taste skill before UI construction.

Use TDD for calculations.

Use E2E for user flow.

---

# 200. M7

Telegram and notifications.

Morning brief.

Journal input.

Sync warnings.

---

# 201. M8

AI Coach.

Implement deterministic tool architecture first.

Then LLM orchestration.

---

# 202. M9

Somatriq MCP.

Desktop access.

Read only default.

Quality and caveat aware results.

---

# 203. M10

Correlations.

Deterministic statistics.

Scientific UI.

---

# 204. M11

Experiments.

N of 1 experiment workflows.

Telegram compliance.

Statistical evaluation.

---

# 205. M12

Strength training model.

Muscular load.

Personal recovery response.

---

# 206. M13

Additional connectors.

Start with highest value source.

Do not add five half working integrations simultaneously.

---

# 207. FIRST DEVELOPMENT ACTIONS

The agent should perform these actions first.

```text
1. Inspect available skills.

2. Read this specification.

3. Run grill-with-docs against architecture only if unresolved contradictions remain.

4. Create CONTEXT.md.

5. Create initial ADRs.

6. Initialize somatriq repository.

7. Initialize NOOP fork separately.

8. Configure official Dokploy MCP.

9. Inspect existing Dokploy project and environment.

10. Create Somatriq project or environment through Dokploy MCP.

11. Build PostgreSQL plus extensions.

12. Build FastAPI skeleton.

13. Write first failing ingest test.

14. Implement one minimal batch ingestion path.

15. Implement one read endpoint.

16. Implement minimal web view.

17. Deploy through Dokploy MCP.

18. Verify end to end.

19. Only then begin mobile integration.
```

---

# 208. INITIAL ADRS

Create immediately:

```text
0001-postgresql-is-system-of-record

0002-timescaledb-for-high-frequency-series

0003-preserve-raw-sensor-input

0004-noop-fork-is-mobile-collector

0005-mobile-is-local-first

0006-server-sync-is-idempotent

0007-dokploy-is-deployment-platform

0008-dokploy-mcp-is-standard-operations-interface

0009-llm-does-not-calculate-statistics

0010-health-mcp-is-domain-scoped

0011-postgresql-is-not-public

0012-derived-values-are-versioned

0013-multiple-data-sources-are-preserved

0014-web-is-primary-rich-interface
```

---

# 209. DOKPLOY ENVIRONMENT VARIABLES

Document all variables in:

```text
.env.example
```

Categories:

```text
database

application

authentication

mobile pairing

AI provider

Telegram

ntfy

backup

MCP

observability
```

Never commit real values.

---

# 210. DOKPLOY MCP CONFIG EXAMPLE

Agent environment should conceptually configure:

```json
{
  "mcpServers": {
    "dokploy": {
      "command": "npx",
      "args": ["-y", "@dokploy/mcp"],
      "env": {
        "DOKPLOY_URL": "${DOKPLOY_URL}",
        "DOKPLOY_API_KEY": "${DOKPLOY_API_KEY}",
        "DOKPLOY_TOOL_PRESET": "deploy"
      }
    }
  }
}
```

Use the exact configuration format required by the actual agent client.

Do not commit API keys.

---

# 211. DOKPLOY VERIFICATION AFTER DEPLOY

Agent must verify:

```text
deployment state healthy

web domain resolves

HTTPS works

API ready endpoint works

MCP authenticated endpoint works

private services are not publicly routed

database has no public port

volumes mounted

migration completed

worker healthy

scheduler healthy
```

Do not equate a green Docker status with successful deployment.

---

# 212. SKILLS INSTALLATION POLICY

If required skills are already installed:

```text
use them
```

If not installed:

```text
identify trusted upstream

inspect license

inspect SKILL.md

install only according to environment policy
```

Do not execute random third party setup scripts blindly.

Preferred skill families include:

```text
grill-with-docs

domain-modeling

tdd

diagnosing-bugs

research

codebase-design

code-review

write-prd when product ambiguity exists

tech-spec when a feature requires implementation planning

agent best practices

design-taste-frontend

impeccable or equivalent design audit
```

---

# 213. SKILL SELECTION EXAMPLES

New database domain:

```text
domain-modeling
grill-with-docs
tech-spec
tdd
code-review
```

Bug:

```text
diagnosing-bugs
tdd
code-review
```

New dashboard:

```text
design-taste-frontend
implementation
accessibility review
design audit
code-review
```

New MCP capability:

```text
agent-best-practices
research
tech-spec
tdd
security review
code-review
```

New Dokploy deployment behavior:

```text
research if needed
Dokploy MCP
verification
runbook update
```

---

# 214. DO NOT OVERUSE SKILLS

Skill usage must improve work.

Do not call every skill for every typo.

Use the smallest relevant skill set.

Keep context lean.

---

# 215. SELF REVIEW BEFORE COMPLETION

Before telling the user a milestone is complete, agent must check:

```text
Does it run?

Does CI pass?

Do tests prove behavior?

Does the deployed version actually respond?

Is migration applied?

Did I verify logs?

Did I update docs?

Did I inspect security implications?

Did I accidentally expose something?

Does the UI look intentionally designed?

Can this change be rolled back?
```

---

# 216. FINAL PRODUCT EXPERIENCE

After months of data the user should be able to open:

```text
https://somatriq.example.com
```

and immediately see:

```text
Recovery

Sleep

HRV

RHR

Training state

Main insight
```

Then open Explore and inspect years of health data.

---

# 217. PC EXPERIENCE

An MCP client should be able to ask:

```text
Analyze my last six months.

Which factors are most strongly associated
with next morning HRV?

Control for sleep duration and training load.
```

Flow:

```text
AI client
 ↓
Somatriq MCP
 ↓
Statistics service
 ↓
PostgreSQL
 ↓
Python
 ↓
structured result
 ↓
AI explanation
```

---

# 218. TELEGRAM EXPERIENCE

User:

```text
How did I sleep?
```

Somatriq returns current validated data.

User:

```text
Coffee now
```

Somatriq logs the event.

User:

```text
Start an experiment where I stop caffeine after 14:00.
```

Somatriq creates a structured experiment after required parameters are established.

---

# 219. PERSONAL SCIENCE EXPERIENCE

The product should eventually answer:

```text
What predicts my HRV?

What changes after heavy training?

How much does bedtime consistency matter for me?

Does late caffeine measurably affect my sleep?

How accurate are our recovery predictions?

Which recovery algorithm best predicts my real outcomes?
```

Using the user's actual longitudinal data.

---

# 220. PROJECT SUCCESS CRITERIA

Somatriq succeeds when:

```text
wearable data survives vendor changes

mobile outages do not lose data

server outages do not lose data

duplicate sync does not duplicate data

raw evidence can be reprocessed

every important metric has provenance

the user can export everything

the user can access data from the web

the user can access analysis through MCP

the user can interact through Telegram

the AI is useful without owning the calculations

experiments are measurable

scores are explainable

deployment is reproducible

Dokploy operations are automated through MCP

the UI feels intentionally designed rather than generated

the codebase remains understandable to both humans and development agents
```

---

# 221. NON NEGOTIABLE RULES

```text
Do not expose PostgreSQL.

Do not make remote connectivity necessary for mobile collection.

Do not allow sync retries to create duplicates.

Do not silently overwrite algorithm semantics.

Do not discard original source measurements.

Do not hide data quality problems.

Do not give an LLM unrestricted SQL.

Do not let an LLM invent numerical calculations.

Do not mix wearable specific logic into the canonical health domain.

Do not ship major UI without a design taste review.

Do not ship deterministic core behavior without tests.

Do not make major architecture decisions without documentation.

Do not operate Dokploy manually when the official MCP can safely perform the task.

Do not use destructive Dokploy MCP operations without explicit authorization.

Do not call a feature finished merely because generated code compiles.
```

---

# 222. PRODUCT NAME

Project:

```text
Somatriq
```

Concept:

```text
Soma
body

Matriq
measurement
matrix
metrics
intelligence
```

Possible tagline:

```text
Your body. Your data. Your intelligence.
```

The product must not present itself as officially affiliated with WHOOP.

WHOOP is a supported hardware source.

Somatriq is an independent personal data platform.

---

# 223. FINAL ARCHITECTURE

```text
                           BODY
                            │
                            ▼
                        WHOOP 4
                            │
                           BLE
                            │
                            ▼
                   SOMATRIQ MOBILE
                            │
                       local SQLite
                            │
                       durable queue
                            │
                           HTTPS
                            │
                            ▼

══════════════════════ DOKPLOY ══════════════════════

                        Traefik
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
         WEB               API               MCP
          │                 │                 │
          └─────────────────┼─────────────────┘
                            │
                   APPLICATION CORE
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
      ANALYTICS           AGENT            WORKERS
          │                 │                 │
          └─────────────────┼─────────────────┘
                            │
                            ▼
                       POSTGRESQL
                       TIMESCALEDB
                         PGVECTOR
                            │
                ┌───────────┴───────────┐
                ▼                       ▼
          CANONICAL DATA            RAW ARCHIVE
                │
     ┌──────────┼────────────┬──────────────┐
     ▼          ▼            ▼              ▼
 TELEGRAM      NTFY      WEB PUSH      EXTERNAL AI

                            │
                            ▼
                         BACKUP
```

Deployment and operational control:

```text
Development Agent
        │
        ▼
Official Dokploy MCP
        │
        ▼
Dokploy
        │
        ▼
Somatriq Compose Stack
```

Engineering loop:

```text
UNDERSTAND DOMAIN
        ↓
GRILL AND DOCUMENT
        ↓
WRITE SPEC
        ↓
RED TEST
        ↓
GREEN
        ↓
REFACTOR
        ↓
CODE REVIEW
        ↓
DESIGN REVIEW IF UI
        ↓
CI
        ↓
DEPLOY THROUGH DOKPLOY MCP
        ↓
VERIFY PRODUCTION BEHAVIOR
        ↓
UPDATE DOCUMENTATION
```

That is the required engineering and product model for Somatriq.
