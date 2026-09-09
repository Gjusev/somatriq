"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { fetchToday } from "../lib/api";
import { useSession } from "../lib/auth";
import type {
  ContributionTone,
  RecoveryContribution,
  RecoveryInput,
  TodayResponse,
} from "../lib/api";

/**
 * The daily anchor (M6, spec §75-76). Every number earns its place: the
 * recovery score only appears when all inputs exist, each contribution is a
 * quiet text row (never a gauge), and missing inputs are listed with a
 * one-line explanation — no confident number without its inputs.
 */

/** Freshness copy flips from "updated …" to "stale …" after this many minutes. */
const STALE_AFTER_MINUTES = 120;

const ISO_DAY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Display labels for the frozen contribution inputs (recovery.py). */
const INPUT_LABELS: Record<RecoveryInput, string> = {
  hrv: "HRV",
  rhr: "RHR",
  sleep: "Sleep",
  temperature: "Temperature",
  training_load: "Training load",
};

/** One honest line per missing input — the panel never explains with silence. */
const MISSING_EXPLANATIONS: Record<RecoveryInput, string> = {
  hrv: "HRV needs RR intervals from a sleep session attributed to this wake-date.",
  rhr: "Resting HR needs enough heart-rate coverage of the day to form an estimate.",
  sleep: "Sleep needs a session attributed to this wake-date.",
  temperature: "Temperature is not a recovery input in v1.",
  training_load: "Training load is not a recovery input in v1.",
};

function explainMissingInput(input: string): string {
  if (Object.hasOwn(MISSING_EXPLANATIONS, input)) {
    return MISSING_EXPLANATIONS[input as RecoveryInput];
  }
  return "waiting for this input to be recorded.";
}

/**
 * Format a payload day from its own components. The wire date is already the
 * local calendar day in the day's effective timezone (ADR 0017), so it must
 * never round-trip through the browser timezone.
 */
function formatDayLabel(date: string): string {
  const match = ISO_DAY_PATTERN.exec(date);
  if (match === null) return date;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (
    !Number.isInteger(year) ||
    !Number.isInteger(month) ||
    !Number.isInteger(day)
  ) {
    return date;
  }
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

function formatDurationMinutes(minutes: number): string {
  const total = Math.round(minutes);
  const hours = Math.floor(total / 60);
  const remainder = total % 60;
  if (hours === 0) return `${remainder} m`;
  return remainder === 0 ? `${hours} h` : `${hours} h ${remainder} m`;
}

function formatFreshness(minutes: number): string {
  if (minutes < 1) return "updated moments ago";
  if (minutes < 60) return `updated ${Math.round(minutes)} min ago`;
  const hours = minutes / 60;
  if (hours < STALE_AFTER_MINUTES / 60) {
    return `updated ${Math.round(hours * 10) / 10} h ago`;
  }
  return `stale ${Math.round(hours)} h ago`;
}

/** The arrow carries the oriented direction; the z is shown verbatim. */
function toneArrow(tone: ContributionTone): string {
  switch (tone) {
    case "positive":
      return "↑";
    case "negative":
      return "↓";
    default:
      return "—";
  }
}

function formatSignedZ(z: number | null): string {
  if (z === null) return "—";
  return `${z < 0 ? "-" : "+"}${Math.abs(z).toFixed(1)}`;
}

function formatContributionValue(input: RecoveryInput, value: number | null): string {
  if (value === null) return "—";
  switch (input) {
    case "hrv":
      return `${Math.round(value)} ms`;
    case "rhr":
      return `${Math.round(value)} bpm`;
    case "sleep":
      return formatDurationMinutes(value);
    default:
      return String(Math.round(value * 10) / 10);
  }
}

/**
 * Missing inputs come from the recovery result when the server names them;
 * when recovery itself is absent they are derived from the summaries, so the
 * honest panel can always say what is absent.
 */
function deriveMissingInputs(data: TodayResponse): string[] {
  if (data.recovery !== null) return data.recovery.missingInputs;
  const missing: string[] = [];
  if (data.hrv === null || data.hrv.rmssdMs === null) missing.push("hrv");
  if (data.restingHr === null) missing.push("rhr");
  if (data.sleep === null || data.sleep.durationMinutes === null) {
    missing.push("sleep");
  }
  return missing;
}

/** Spec §76 explainability block: quiet text rows, one per contribution. */
function ContributionList({ contributions }: { contributions: RecoveryContribution[] }) {
  return (
    <div className="today-explain">
      <p className="today-subhead">Contributions</p>
      {contributions.map((row) => (
        <div className="contribution-row" key={row.input}>
          <span className="contribution-input">{INPUT_LABELS[row.input]}</span>
          <span className="contribution-value num">
            {formatContributionValue(row.input, row.value)}
          </span>
          <span className="contribution-detail">
            <span className="num">
              {toneArrow(row.contribution)} {formatSignedZ(row.robustZ)}
            </span>
            {" · "}
            {row.contribution}
            {row.note !== null && ` · ${row.note}`}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function TodayCard() {
  const { ready, token } = useSession();

  const query = useQuery({
    queryKey: ["metrics", "today"],
    queryFn: fetchToday,
    enabled: ready && token !== null,
    staleTime: 60_000,
    refetchInterval: 120_000,
  });

  const data = query.data;

  const recovery = data?.recovery ?? null;
  const recoveryScore = recovery?.score ?? null;
  const missing = data === undefined ? [] : deriveMissingInputs(data);
  const caveats =
    data === undefined ? [] : [...data.caveats, ...(recovery?.caveats ?? [])];

  const errorReason =
    query.error instanceof Error ? query.error.message : "request failed";

  return (
    <section className="card" aria-label="Today — recovery and nightly summary">
      <header className="card-header">
        <h3>Today</h3>
        {data !== undefined && (
          <p className="card-meta">
            {formatDayLabel(data.date)} · {data.timezone}
          </p>
        )}
      </header>

      {!ready && (
        <div className="skeleton" role="status" aria-label="Loading today's summary">
          <div className="skeleton-bar" style={{ width: "38%" }} />
          <div className="skeleton-bar" style={{ width: "72%" }} />
          <div className="skeleton-bar" style={{ width: "56%" }} />
        </div>
      )}

      {ready && token === null && (
        <div className="state">
          <p className="state-message">
            Sign in to see your recovery, sleep and HRV — health reads answer
            the owner&apos;s session only.
          </p>
          <Link href="/login/" className="btn">
            Sign in
          </Link>
        </div>
      )}

      {ready && token !== null && query.isPending && (
        <div className="skeleton" role="status" aria-label="Loading today's summary">
          <div className="skeleton-bar" style={{ width: "38%" }} />
          <div className="skeleton-bar" style={{ width: "72%" }} />
          <div className="skeleton-bar" style={{ width: "56%" }} />
        </div>
      )}

      {ready && token !== null && query.isError && (
        <div className="state" role="alert">
          <p className="state-message">
            Could not load today&apos;s summary — {errorReason}. The API may be
            unreachable or returned an unexpected response.
          </p>
          <button
            type="button"
            className="btn"
            onClick={() => void query.refetch()}
          >
            Retry
          </button>
        </div>
      )}

      {data !== undefined && (
        <>
          {recoveryScore !== null ? (
            <div className="today-headline">
              <div>
                <p className="today-score num">{Math.round(recoveryScore)}</p>
                <p className="today-score-caption">recovery · 0–100</p>
              </div>
              {recovery !== null && recovery.contributions.length > 0 && (
                <ContributionList contributions={recovery.contributions} />
              )}
            </div>
          ) : (
            <div className="state">
              <p className="state-message">
                Recovery needs HRV, RHR and sleep —{" "}
                {missing.length > 0 ? (
                  <>
                    missing: <span className="num">{missing.join(", ")}</span>.
                  </>
                ) : (
                  "no score yet."
                )}
              </p>
              {missing.length > 0 && (
                <ul className="missing-list">
                  {missing.map((input) => (
                    <li key={input}>
                      <span className="num">{input}</span> —{" "}
                      {explainMissingInput(input)}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {recoveryScore === null &&
            recovery !== null &&
            recovery.contributions.length > 0 && (
              <ContributionList contributions={recovery.contributions} />
            )}

          <dl className="stat-lines">
            <div className="stat-line">
              <dt>Resting HR</dt>
              <dd className="num">
                {data.restingHr === null
                  ? "—"
                  : `${Math.round(data.restingHr)} bpm`}
                {data.restingHrQuality !== null && (
                  <span className="badge">{data.restingHrQuality}</span>
                )}
              </dd>
            </div>

            <div className="stat-line">
              <dt>Sleep</dt>
              <dd className="num">
                {data.sleep === null || data.sleep.durationMinutes === null
                  ? "—"
                  : formatDurationMinutes(data.sleep.durationMinutes)}
                {data.sleep?.efficiency != null &&
                  ` · ${Math.round(data.sleep.efficiency * 100)}% eff`}
              </dd>
            </div>

            <div className="stat-line">
              <dt>HRV RMSSD</dt>
              <dd className="num">
                {data.hrv === null || data.hrv.rmssdMs === null
                  ? "—"
                  : `${Math.round(data.hrv.rmssdMs)} ms`}
              </dd>
              {data.hrv !== null && (data.hrv.rmssdMs !== null || data.hrv.samples > 0) && (
                <dd className="stat-line-note num">
                  {data.hrv.validSamples.toLocaleString()} /{" "}
                  {data.hrv.samples.toLocaleString()} RR valid ·{" "}
                  {Math.round(data.hrv.coverage * 100)}% coverage
                </dd>
              )}
            </div>

            <div className="stat-line">
              <dt>Freshness</dt>
              <dd className="num">
                {data.dataFreshnessMinutes === null
                  ? "—"
                  : formatFreshness(data.dataFreshnessMinutes)}
              </dd>
            </div>
          </dl>

          {caveats.length > 0 && <p className="caveats">{caveats.join(" ")}</p>}

          {recovery !== null && (
            <p className="caveats num">recovery: {recovery.algorithmVersion}</p>
          )}
          {data.hrv !== null && (
            <p className="caveats num">
              hrv: {data.hrv.algorithmVersion} · filter: {data.hrv.filterVersion}
            </p>
          )}
        </>
      )}
    </section>
  );
}
