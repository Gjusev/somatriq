"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useQuery } from "@tanstack/react-query";
import { fetchTrainingResponse, fetchTrainingSessions } from "../lib/api";
import type { TrainingResponse, TrainingSession, TrainingWeek } from "../lib/api";
import { useSession } from "../lib/auth";
import { readChartPalette, useColorSchemeVersion } from "../lib/chart-palette";

/**
 * Strength training (M12, spec §78-80, §205): the muscular-load layer.
 * Recent sessions with their deterministic summaries (tonnage, hard sets),
* a quiet weekly-tonnage trend, and the §80 personal response — how a
 * training day's load co-moves with NEXT-day recovery. The response rows
 * are correlational and the causal note is the card's persistent footer,
 * exactly like the correlations card (spec §82). Login-gated like the
 * experiments card; tokens only, no new CSS.
 */

/** Session window (days) — four ISO weeks of context. */
const SESSIONS_DAYS = 28;
/** §80 response window — the correlations matrix default (spec §203). */
const RESPONSE_DAYS = 90;

const MONO = 'ui-monospace, "Cascadia Mono", monospace';

function formatR(value: number): string {
  return (value >= 0 ? "+" : "") + value.toFixed(2);
}

function formatKg(value: number): string {
  return `${Math.round(value).toLocaleString()} kg`;
}

function formatDay(ts: string): string {
  const when = new Date(ts);
  return when.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}


/** Quiet weekly-tonnage line: one point per ISO week, no animation (§177). */
function buildWeeklyOption(
  palette: { ink: string; muted: string; paper: string; accent: string },
  weekly: TrainingWeek[],
): EChartsOption {
  return {
    animation: false,
    grid: { left: 8, right: 8, top: 16, bottom: 0, containLabel: true },
    xAxis: {
      type: "category",
      data: weekly.map((week) => week.week),
      boundaryGap: false,
      axisLine: { lineStyle: { color: palette.muted, opacity: 0.5 } },
      axisTick: { show: false },
      axisLabel: {
        color: palette.muted,
        fontFamily: MONO,
        fontSize: 11,
        hideOverlap: true,
      },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      name: "kg",
      nameTextStyle: {
        color: palette.muted,
        fontFamily: MONO,
        fontSize: 11,
        align: "right",
      },
      axisLabel: { color: palette.muted, fontFamily: MONO, fontSize: 11 },
      splitLine: { lineStyle: { color: palette.muted, opacity: 0.18 } },
    },
    tooltip: {
      trigger: "axis",
      backgroundColor: palette.paper,
      borderColor: palette.muted,
      borderWidth: 1,
      padding: [6, 10],
      textStyle: { color: palette.ink, fontFamily: MONO, fontSize: 12 },
      axisPointer: {
        type: "line",
        lineStyle: { color: palette.muted, opacity: 0.4 },
      },
    },
    series: [
      {
        name: "Weekly tonnage",
        type: "line",
        data: weekly.map((week) => Math.round(week.tonnageKg)),
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 1.5, color: palette.accent },
        itemStyle: { color: palette.accent },
        areaStyle: { color: palette.accent, opacity: 0.1 },
      },
    ],
  };
}

function SessionsTable({ sessions }: { sessions: TrainingSession[] }) {
  return (
    <table className="corr-table">
      <thead>
        <tr>
          <th scope="col">session</th>
          <th scope="col" className="corr-num-col">
            sets
          </th>
          <th scope="col" className="corr-num-col">
            tonnage
          </th>
          <th scope="col" className="corr-num-col">
            hard
          </th>
        </tr>
      </thead>
      <tbody>
        {sessions.map((session) => (
          <tr key={session.id}>
            <td className="corr-pair">
              {formatDay(session.ts)} · {session.summary.exercises.join(", ")}
              {session.summary.bodyweightSets > 0 && (
                <> · {session.summary.bodyweightSets} bodyweight</>
              )}
            </td>
            <td className="num corr-num-col">{session.summary.setCount}</td>
            <td className="num corr-num-col">{formatKg(session.summary.tonnageKg)}</td>
            <td className="num corr-num-col">{session.summary.hardSets}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// useQuery's return type is the shared shape the table above renders.
type useSessionsQuery = ReturnType<
  typeof useQuery<ReturnType<typeof fetchTrainingSessions>, Error>
>;

function ResponseSection({ response }: { response: TrainingResponse }) {
  const rows = [...response.pairs].sort((a, b) => Math.abs(b.r) - Math.abs(a.r));
  return (
    <>
      <p className="card-meta">
        Personal response — training day vs next-day recovery
      </p>
      {rows.length === 0 ? (
        <p className="caveats num">
          No response coefficients yet — a pair needs at least 14 shared days
          of training and recovery data.
        </p>
      ) : (
        <table className="corr-table">
          <thead>
            <tr>
              <th scope="col">pair (+1 day)</th>
              <th scope="col" className="corr-num-col">
                n
              </th>
              <th scope="col" className="corr-num-col">
                r
              </th>
              <th scope="col">band</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.pair[0] + "|" + row.pair[1]}>
                <td className="corr-pair">
                  {row.pair[0]} × {row.pair[1]}
                </td>
                <td className="num corr-num-col">{row.n}</td>
                <td
                  className={`num corr-num-col ${row.r >= 0 ? "corr-r-pos" : "corr-r-neg"}`}
                >
                  {formatR(row.r)}
                  {row.significant ? "*" : ""}
                </td>
                <td className="corr-band">{row.band}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {response.skipped.length > 0 && (
        <details className="corr-skipped">
          <summary>
            insufficient data — {response.skipped.length} pair
            {response.skipped.length === 1 ? "" : "s"} skipped
          </summary>
          <ul>
            {response.skipped.map((row) => (
              <li key={row.pair[0] + "|" + row.pair[1]}>
                <span className="corr-pair">
                  {row.pair[0]} × {row.pair[1]}
                </span>{" "}
                — {row.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
      <p className="caveats num">
        {response.nTests} test{response.nTests === 1 ? "" : "s"} ·{" "}
        α&#8203;={response.bonferroniAlpha.toFixed(5)} (Bonferroni) ·{" "}
        {response.method}
      </p>
      <p className="corr-note">{response.note}</p>
    </>
  );
}

export default function TrainingCard() {
  const { ready, token } = useSession();
  const schemeVersion = useColorSchemeVersion();
  const palette = useMemo(readChartPalette, [schemeVersion]);

  const sessionsQuery = useQuery({
    queryKey: ["training", "sessions", { days: SESSIONS_DAYS }],
    queryFn: () => fetchTrainingSessions(SESSIONS_DAYS),
    enabled: ready && token !== null,
    staleTime: 60_000,
  });

  const responseQuery = useQuery({
    queryKey: ["training", "response", { days: RESPONSE_DAYS }],
    queryFn: () => fetchTrainingResponse(RESPONSE_DAYS),
    enabled: ready && token !== null,
    staleTime: 300_000,
  });

  const sessions = sessionsQuery.data?.sessions ?? [];
  const weekly = useMemo(
    () => sessionsQuery.data?.weekly ?? [],
    [sessionsQuery.data],
  );
  const weeklyOption = useMemo(
    () => (weekly.length >= 2 ? buildWeeklyOption(palette, weekly) : null),
    [palette, weekly],
  );

  const sessionsError =
    sessionsQuery.error instanceof Error
      ? sessionsQuery.error.message
      : "request failed";

  return (
    <section className="card" id="training" aria-label="Strength training">
      <header className="card-header">
        <h3>Strength training</h3>
        {sessionsQuery.data !== undefined && (
          <p className="card-meta num">
            last {sessionsQuery.data.days} days ·{" "}
            {formatKg(weekly.reduce((sum, week) => sum + week.tonnageKg, 0))} tonnage
          </p>
        )}
      </header>

      {!ready && (
        <div className="skeleton" role="status" aria-label="Loading training data">
          <div className="skeleton-bar" style={{ width: "44%" }} />
          <div className="skeleton-bar" style={{ width: "30%" }} />
        </div>
      )}

      {ready && token === null && (
        <div className="state">
          <p className="state-message">
            Sign in to see your strength training log — sessions, tonnage, and
            how training days relate to next-day recovery.
          </p>
          <Link href="/login/" className="btn">
            Sign in
          </Link>
        </div>
      )}

      {ready && token !== null && sessionsQuery.isError && (
        <div className="state" role="alert">
          <p className="state-message">
            Could not load training sessions — {sessionsError}. The API may be
            unreachable or returned an unexpected response.
          </p>
          <button
            type="button"
            className="btn"
            onClick={() => void sessionsQuery.refetch()}
          >
            Retry
          </button>
        </div>
      )}

      {ready && token !== null && sessionsQuery.isPending && (
        <div className="skeleton" role="status" aria-label="Loading training data">
          <div className="skeleton-bar" style={{ width: "44%" }} />
          <div className="skeleton-bar" style={{ width: "58%" }} />
          <div className="skeleton-bar" style={{ width: "36%" }} />
        </div>
      )}

      {sessionsQuery.data !== undefined && sessions.length === 0 && (
        <div className="state">
          <p className="state-message">
            No strength sessions yet — log one from Telegram with a training
            line like <code>/log Chest press 180x8 170x9 160x10</code>.
          </p>
        </div>
      )}

      {sessionsQuery.data !== undefined && sessions.length > 0 && (
        <>
          <SessionsTable sessions={sessions} />
          {weeklyOption !== null && (
            <figure className="chart-figure">
              <div
                className="chart-frame"
                role="img"
                aria-label={`Weekly tonnage line chart over ${weekly.length} ISO weeks.`}
              >
                <ReactECharts
                  option={weeklyOption}
                  notMerge={true}
                  lazyUpdate={true}
                  style={{ width: "100%", height: "100%" }}
                />
              </div>
            </figure>
          )}
        </>
      )}

      {ready && token !== null && responseQuery.data !== undefined && (
        <ResponseSection response={responseQuery.data} />
      )}
    </section>
  );
}
