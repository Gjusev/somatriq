"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchPlanToday,
  fetchPreferences,
  updatePreferences,
  type PlanTodayResponse,
  type SleepNeedContribution,
} from "../lib/api";
import { useSession } from "../lib/auth";

/**
 * The day's action surface (Block 1). Same honesty rules as Today: no
 * fabricated numbers — a missing anchor renders an explicit "not yet"
 * line, the debt always shows its window and cap, and every element is a
 * quiet text row, never a gauge. The wake time is the one preference the
 * plan consumes (ADR 0019): edited inline, saved through the typed store.
 */

const TIER_COPY: Record<string, string> = {
  rest: "Rest — recovery is the session",
  light: "Light — keep it easy",
  moderate: "Moderate — normal training",
  hard: "Hard — go for it",
};

const NEED_INPUT_LABELS: Record<SleepNeedContribution["input"], string> = {
  baseline: "Sleep baseline",
  sleep_debt: "Sleep debt",
  recent_load: "Yesterday's strain",
  recovery: "Recovery",
};

function formatMinutes(minutes: number): string {
  const total = Math.round(minutes);
  const hours = Math.floor(total / 60);
  const remainder = total % 60;
  if (hours === 0) return `${remainder} min`;
  return remainder === 0 ? `${hours} h` : `${hours} h ${remainder} min`;
}

/** "22:45:00" -> "22:45" — local clock display only, never recomputed. */
function formatClock(clock: string): string {
  return clock.length >= 5 ? clock.slice(0, 5) : clock;
}

function NeedExplain({ need }: { need: PlanTodayResponse["sleepNeed"] }) {
  return (
    <div className="today-explain">
      <p className="today-subhead">Sleep need — contributions</p>
      {need.contributions.map((row) => (
        <div className="contribution-row" key={row.input}>
          <span className="contribution-input">{NEED_INPUT_LABELS[row.input]}</span>
          <span className="contribution-value num">
            {row.value === null
              ? "—"
              : row.input === "baseline" || row.input === "sleep_debt"
                ? formatMinutes(row.value)
                : String(Math.round(row.value))}
          </span>
          <span className="contribution-detail">
            {row.minutesAdded === null ? (
              "missing — not imputed"
            ) : (
              <span className="num">+{Math.round(row.minutesAdded)} min</span>
            )}
            {row.note !== null && ` · ${row.note}`}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function PlanCard() {
  const { ready, token } = useSession();
  const queryClient = useQueryClient();

  const planQuery = useQuery({
    queryKey: ["plan", "today"],
    queryFn: fetchPlanToday,
    enabled: ready && token !== null,
    staleTime: 60_000,
  });

  const preferencesQuery = useQuery({
    queryKey: ["preferences"],
    queryFn: fetchPreferences,
    enabled: ready && token !== null,
    staleTime: 300_000,
  });

  const savePreference = useMutation({
    mutationFn: (wakeTime: string) => updatePreferences({ wakeTime }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["preferences"] });
      await queryClient.invalidateQueries({ queryKey: ["plan", "today"] });
    },
  });

  const data = planQuery.data;
  const errorReason =
    planQuery.error instanceof Error ? planQuery.error.message : "request failed";

  return (
    <section className="card" aria-label="Today plan — guidance and bedtime">
      <header className="card-header">
        <h3>Today plan</h3>
        {data !== undefined && (
          <p className="card-meta">
            {data.date} · {data.timezone}
          </p>
        )}
      </header>

      {!ready && (
        <div className="skeleton" role="status" aria-label="Loading today's plan">
          <div className="skeleton-bar" style={{ width: "44%" }} />
          <div className="skeleton-bar" style={{ width: "68%" }} />
        </div>
      )}

      {ready && token === null && (
        <div className="state">
          <p className="state-message">
            Sign in to see your plan — guidance answers the owner&apos;s
            session only.
          </p>
          <Link href="/login/" className="btn">
            Sign in
          </Link>
        </div>
      )}

      {ready && token !== null && planQuery.isPending && (
        <div className="skeleton" role="status" aria-label="Loading today's plan">
          <div className="skeleton-bar" style={{ width: "44%" }} />
          <div className="skeleton-bar" style={{ width: "68%" }} />
        </div>
      )}

      {ready && token !== null && planQuery.isError && (
        <div className="state" role="alert">
          <p className="state-message">
            Could not load the plan — {errorReason}. The API may be
            unreachable or returned an unexpected response.
          </p>
          <button
            type="button"
            className="btn"
            onClick={() => void planQuery.refetch()}
          >
            Retry
          </button>
        </div>
      )}

      {data !== undefined && (
        <>
          <dl className="stat-lines">
            <div className="stat-line">
              <dt>Guidance</dt>
              <dd>
                {data.plan.tier === null ? (
                  "not available yet — recovery not earned"
                ) : (
                  <>
                    <span className="badge">{data.plan.tier}</span>{" "}
                    {TIER_COPY[data.plan.tier]}
                  </>
                )}
              </dd>
            </div>

            <div className="stat-line">
              <dt>Target strain</dt>
              <dd className="num">
                {data.plan.targetStrain === null
                  ? "—"
                  : `${data.plan.targetStrain.min.toFixed(1)}–${data.plan.targetStrain.max.toFixed(1)}`}
                {data.plan.targetStrain === null && data.plan.tier !== null && (
                  <span className="stat-line-note">
                    {" "}· building strain history
                  </span>
                )}
              </dd>
            </div>

            <div className="stat-line">
              <dt>Bedtime window</dt>
              <dd className="num">
                {data.plan.bedtimeWindow === null
                  ? "—"
                  : `${formatClock(data.plan.bedtimeWindow.start)}–${formatClock(data.plan.bedtimeWindow.end)}`}
              </dd>
            </div>

            <div className="stat-line">
              <dt>Sleep need</dt>
              <dd className="num">
                {data.sleepNeed.minutes === null
                  ? "— (baseline not built yet)"
                  : formatMinutes(data.sleepNeed.minutes)}
              </dd>
            </div>

            <div className="stat-line">
              <dt>Sleep debt</dt>
              <dd className="num">
                {data.sleepDebt.debtMin === null
                  ? "unknown — no measured nights"
                  : `${Math.round(data.sleepDebt.debtMin)} min`}
                <span className="stat-line-note">
                  {" "}· 7 nights, capped at 4 h · never banks credit
                </span>
              </dd>
            </div>
          </dl>

          {data.sleepNeed.minutes !== null && <NeedExplain need={data.sleepNeed} />}

          {preferencesQuery.data !== undefined && (
            <form
              className="pref-row"
              onSubmit={(event) => {
                event.preventDefault();
                const input = event.currentTarget.elements.namedItem("wake_time");
                if (input instanceof HTMLInputElement && input.value !== "") {
                  savePreference.mutate(input.value);
                }
              }}
            >
              <label htmlFor="wake-time-input">Wake time</label>
              <input
                id="wake-time-input"
                name="wake_time"
                type="time"
                defaultValue={preferencesQuery.data.wakeTime ?? data.wakeTime}
                key={preferencesQuery.data.wakeTime ?? "default"}
              />
              <button type="submit" className="btn" disabled={savePreference.isPending}>
                {savePreference.isPending ? "Saving…" : "Save"}
              </button>
              <span className="stat-line-note">
                {savePreference.isError
                  ? "save failed — try again"
                  : data.wakeSource === "preference"
                    ? "your preference"
                    : "default 07:00"}
              </span>
            </form>
          )}

          {data.caveats.length > 0 && <p className="caveats">{data.caveats.join(" ")}</p>}

          <p className="caveats num">
            plan: {data.plan.algorithmVersion} · need: {data.sleepNeed.algorithmVersion}
          </p>
        </>
      )}
    </section>
  );
}
