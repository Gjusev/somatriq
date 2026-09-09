"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createExperiment,
  deleteJournalEvent,
  experimentDraftFromInsight,
  fetchBehaviorInsights,
  fetchJournalDay,
  isDeletableKind,
  logJournalEvent,
  type BehaviorInsight,
  type JournalEvent,
} from "../lib/api";
import { useSession } from "../lib/auth";

/**
 * The journal as laboratory input (Block 2): one tap per behavior, notes
 * kept verbatim, and the transparent insight panel — medians, n, p, q and
 * confounders beside every effect, keep-logging rows instead of numbers
 * under the gate, and a convert-to-experiment button that starts the N-of-1
 * loop with the insight prefilled. Association language only.
 */

const BEHAVIOR_CHIPS: { kind: string; label: string }[] = [
  { kind: "caffeine", label: "Caffeine" },
  { kind: "alcohol", label: "Alcohol" },
  { kind: "medication", label: "Medication" },
  { kind: "stress", label: "Stress" },
  { kind: "meal", label: "Meal" },
  { kind: "travel", label: "Travel" },
];

function formatEventTime(ts: string): string {
  const parsed = new Date(ts);
  if (Number.isNaN(parsed.getTime())) return ts;
  return parsed.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function InsightRow({ row }: { row: BehaviorInsight }) {
  const queryClient = useQueryClient();
  const convert = useMutation({
    mutationFn: () => createExperiment(experimentDraftFromInsight(row)),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["experiments"] });
    },
  });

  if (row.status === "keep_logging") {
    return (
      <li className="contribution-row">
        <span className="contribution-input">{row.behavior}</span>
        <span className="contribution-detail">keep logging — {row.note ?? "not enough days yet"}</span>
      </li>
    );
  }

  return (
    <li className="contribution-row">
      <span className="contribution-input">
        {row.behavior} → {row.outcome}
      </span>
      <span className="contribution-value num">
        {row.medianDifference === null ? "—" : `${row.medianDifference > 0 ? "+" : ""}${row.medianDifference.toFixed(1)}`}
      </span>
      <span className="contribution-detail">
        <span className="num">
          {row.medianExposed?.toFixed(1) ?? "—"} vs {row.medianUnexposed?.toFixed(1) ?? "—"} · p=
          {row.pValue?.toFixed(3)} · q={row.qValue?.toFixed(3)} · n={row.nExposed}+{row.nUnexposed}
        </span>
        {row.confounders.overlapDays &&
          Object.keys(row.confounders.overlapDays).length > 0 &&
          " · overlaps: " +
            Object.entries(row.confounders.overlapDays)
              .filter(([, count]) => count > 0)
              .map(([behavior, count]) => `${behavior}×${count}`)
              .join(", ")}
      </span>
      <button
        type="button"
        className="btn"
        disabled={convert.isPending}
        onClick={() => convert.mutate()}
        title={row.method}
      >
        {convert.isPending ? "Creating…" : convert.isSuccess ? "Experiment created" : "Convert to experiment"}
      </button>
      {convert.isError && (
        <span className="stat-line-note" role="alert">
          creation failed — try again
        </span>
      )}
    </li>
  );
}

export default function JournalCard() {
  const { ready, token } = useSession();
  const queryClient = useQueryClient();

  const dayQuery = useQuery({
    queryKey: ["journal", "day"],
    queryFn: () => fetchJournalDay(),
    enabled: ready && token !== null,
    staleTime: 30_000,
  });

  const insightsQuery = useQuery({
    queryKey: ["journal", "insights"],
    queryFn: () => fetchBehaviorInsights(90),
    enabled: ready && token !== null,
    staleTime: 300_000,
  });

  const log = useMutation({
    mutationFn: logJournalEvent,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["journal", "day"] });
    },
  });

  const remove = useMutation({
    mutationFn: deleteJournalEvent,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["journal", "day"] });
    },
  });

  const events: JournalEvent[] = dayQuery.data?.events ?? [];
  const okRows = (insightsQuery.data?.rows ?? []).filter((row) => row.status === "ok");
  const pendingRows = (insightsQuery.data?.rows ?? []).filter(
    (row) => row.status === "keep_logging"
  );

  const errorReason =
    dayQuery.error instanceof Error ? dayQuery.error.message : "request failed";

  return (
    <section className="card" aria-label="Journal — quick log and behavior insights">
      <header className="card-header">
        <h3>Journal</h3>
        {dayQuery.data !== undefined && <p className="card-meta">{dayQuery.data.date}</p>}
      </header>

      {!ready && (
        <div className="skeleton" role="status" aria-label="Loading journal">
          <div className="skeleton-bar" style={{ width: "52%" }} />
          <div className="skeleton-bar" style={{ width: "74%" }} />
        </div>
      )}

      {ready && token === null && (
        <div className="state">
          <p className="state-message">
            Sign in to log behaviors — the journal answers the owner&apos;s session only.
          </p>
          <Link href="/login/" className="btn">
            Sign in
          </Link>
        </div>
      )}

      {ready && token !== null && (
        <>
          <div className="journal-chips" role="group" aria-label="Quick-log behaviors">
            {BEHAVIOR_CHIPS.map((chip) => (
              <button
                key={chip.kind}
                type="button"
                className="btn"
                disabled={log.isPending}
                onClick={() => log.mutate({ kind: chip.kind })}
              >
                {chip.label}
              </button>
            ))}
          </div>

          <form
            className="pref-row"
            onSubmit={(event) => {
              event.preventDefault();
              const input = event.currentTarget.elements.namedItem("journal_text");
              if (input instanceof HTMLInputElement && input.value.trim() !== "") {
                log.mutate({ kind: "journal", text: input.value.trim() });
                input.value = "";
              }
            }}
          >
            <label htmlFor="journal-note-input">Note</label>
            <input
              id="journal-note-input"
              name="journal_text"
              type="text"
              maxLength={400}
              placeholder="anything worth remembering"
            />
            <button type="submit" className="btn" disabled={log.isPending}>
              Log
            </button>
            {log.isError && (
              <span className="stat-line-note" role="alert">
                log failed — try again
              </span>
            )}
          </form>

          {dayQuery.isError && (
            <div className="state" role="alert">
              <p className="state-message">Could not load today&apos;s journal — {errorReason}.</p>
              <button type="button" className="btn" onClick={() => void dayQuery.refetch()}>
                Retry
              </button>
            </div>
          )}

          {events.length > 0 && (
            <ul className="missing-list">
              {events.map((event) => (
                <li key={event.id}>
                  <span className="num">{formatEventTime(event.ts)}</span> ·{" "}
                  <span className="badge">{event.kind}</span>
                  {event.text !== null && ` · ${event.text}`}
                  {isDeletableKind(event.kind) && (
                    <button
                      type="button"
                      className="btn"
                      disabled={remove.isPending}
                      onClick={() => remove.mutate(event.id)}
                      aria-label={`Delete ${event.kind} event`}
                    >
                      ✕
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}

          {insightsQuery.data !== undefined && (
            <div className="today-explain">
              <p className="today-subhead">Behavior insights — 90 days</p>
              {okRows.length === 0 ? (
                <p className="state-message">
                  No behavior has 7+ exposed and 7+ unexposed days yet — keep logging.
                </p>
              ) : (
                <ul className="missing-list">
                  {okRows.map((row) => (
                    <li key={`${row.behavior}-${row.outcome}`}>
                      <InsightRow row={row} />
                    </li>
                  ))}
                </ul>
              )}
              {pendingRows.length > 0 && (
                <details>
                  <summary className="stat-line-note">
                    {pendingRows.length} behavior(s) still below the evidence gate
                  </summary>
                  <ul className="missing-list">
                    {pendingRows.map((row) => (
                      <li key={`${row.behavior}-${row.outcome}`}>
                        <InsightRow row={row} />
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              <p className="caveats">{insightsQuery.data.note}</p>
            </div>
          )}
        </>
      )}
    </section>
  );
}
