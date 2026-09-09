"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createAnnotation,
  deleteAnnotation,
  fetchAnnotations,
  fetchExploreContext,
  fetchExploreSeries,
} from "../lib/api";
import { useSession } from "../lib/auth";
import SiteHeader from "../components/site-header";

/**
 * The longitudinal surface (Block 3, grill P12): years of history at day
 * grain with the honesty rules kept visible — gaps stay gaps, overlays
 * mark instrument boundaries and algorithm versions, annotations are the
 * owner's narrative. Week/month/year zoom aggregates client-side; the
 * server contract never changes.
 */

const METRIC_OPTIONS = [
  "resting_hr",
  "hr_mean",
  "avg_hrv",
  "recovery",
  "strain",
  "total_sleep_min",
  "spo2_pct",
  "skin_temp_dev_c",
  "resp_rate_bpm",
  "caffeine_count",
] as const;

const RANGES: { label: string; days: number }[] = [
  { label: "90 days", days: 90 },
  { label: "6 months", days: 182 },
  { label: "1 year", days: 365 },
  { label: "3 years", days: 1096 },
];

const CHART_W = 860;
const CHART_H = 220;
const CHART_PAD = 28;

function isoDaysAgo(days: number): string {
  const day = new Date();
  day.setUTCDate(day.getUTCDate() - days);
  return day.toISOString().slice(0, 10);
}

/** One polyline per contiguous run of days — gaps break the line, never
 * get interpolated (spec §158). */
function gapHonestSegments(
  days: { date: string; value: number }[],
  min: number,
  max: number,
  from: string,
  to: string,
): string[] {
  if (days.length === 0 || max <= min) return [];
  const fromMs = Date.parse(`${from}T00:00:00Z`);
  const toMs = Date.parse(`${to}T00:00:00Z`);
  const span = Math.max(toMs - fromMs, 1);
  const x = (date: string) =>
    CHART_PAD + ((Date.parse(`${date}T00:00:00Z`) - fromMs) / span) * (CHART_W - 2 * CHART_PAD);
  const y = (value: number) =>
    CHART_H - CHART_PAD - ((value - min) / (max - min)) * (CHART_H - 2 * CHART_PAD);

  const segments: string[] = [];
  let current: string[] = [];
  let previousDate = "";
  for (const day of days) {
    if (previousDate !== "" && day.date > previousDate) {
      const gap = Date.parse(`${day.date}T00:00:00Z`) - Date.parse(`${previousDate}T00:00:00Z`);
      if (gap > 86400000 * 1.5) {
        segments.push(current.join(" "));
        current = [];
      }
    }
    current.push(`${x(day.date).toFixed(1)},${y(day.value).toFixed(1)}`);
    previousDate = day.date;
  }
  if (current.length > 0) segments.push(current.join(" "));
  return segments;
}

export default function ExplorePage() {
  const { ready, token } = useSession();
  const queryClient = useQueryClient();
  const [metric, setMetric] = useState<string>("resting_hr");
  const [rangeDays, setRangeDays] = useState(365);
  const [title, setTitle] = useState("");
  const [anchor, setAnchor] = useState("");

  const fromDate = useMemo(() => isoDaysAgo(rangeDays), [rangeDays]);
  const toDate = useMemo(() => isoDaysAgo(0), []);

  const seriesQuery = useQuery({
    queryKey: ["explore", "series", metric, rangeDays],
    queryFn: () => fetchExploreSeries([metric], fromDate, toDate),
    enabled: ready && token !== null,
    staleTime: 120_000,
  });
  const contextQuery = useQuery({
    queryKey: ["explore", "context", rangeDays],
    queryFn: () => fetchExploreContext(fromDate, toDate),
    enabled: ready && token !== null,
    staleTime: 300_000,
  });
  const annotationsQuery = useQuery({
    queryKey: ["annotations"],
    queryFn: fetchAnnotations,
    enabled: ready && token !== null,
    staleTime: 60_000,
  });

  const annotate = useMutation({
    mutationFn: () =>
      createAnnotation({ date_from: anchor || toDate, title, note: null }),
    onSuccess: async () => {
      setTitle("");
      setAnchor("");
      await queryClient.invalidateQueries({ queryKey: ["annotations"] });
    },
  });
  const remove = useMutation({
    mutationFn: deleteAnnotation,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["annotations"] });
    },
  });

  const signedIn = ready && token !== null;
  const series = seriesQuery.data?.metrics[0];
  const context = contextQuery.data;
  const values = series?.days ?? [];
  const min = values.length > 0 ? Math.min(...values.map((day) => day.value)) : 0;
  const max = values.length > 0 ? Math.max(...values.map((day) => day.value)) : 1;
  const segments = gapHonestSegments(values, min, max, fromDate, toDate);

  return (
    <main className="shell">
      <SiteHeader />
      <header className="dashboard-intro">
        <div>
          <p className="eyebrow">Longitudinal evidence</p>
          <h1>Explore.</h1>
        </div>
        <p className="dashboard-intro-copy">
          Day-grain history with gaps kept visible, instrument boundaries and
          algorithm versions marked, and your own annotations pinned.
        </p>
      </header>

      {!signedIn ? (
        <div className="card">
          <div className="state">
            <p className="state-message">Sign in to explore your history.</p>
            <Link href="/login/" className="btn">Sign in</Link>
          </div>
        </div>
      ) : (
        <div className="dashboard">
          <section className="card" aria-label="Explore timeline">
            <header className="card-header">
              <h3>Timeline</h3>
              {series && (
                <p className="card-meta">
                  {series.source_kind}
                  {series.dominant_device !== null && ` · ${series.dominant_device}`} ·{" "}
                  {values.length} measured days · range {min.toFixed(1)}–{max.toFixed(1)}
                </p>
              )}
            </header>

            <div className="pref-row">
              <label htmlFor="explore-metric">Metric</label>
              <select
                id="explore-metric"
                value={metric}
                onChange={(event) => setMetric(event.target.value)}
              >
                {METRIC_OPTIONS.map((option) => (
                  <option key={option} value={option}>{option}</option>
                ))}
              </select>
              {RANGES.map((range) => (
                <button
                  key={range.days}
                  type="button"
                  className="btn"
                  aria-pressed={rangeDays === range.days}
                  onClick={() => setRangeDays(range.days)}
                >
                  {range.label}
                </button>
              ))}
            </div>

            {seriesQuery.isPending && (
              <div className="skeleton" role="status" aria-label="Loading series">
                <div className="skeleton-bar" style={{ width: "70%" }} />
                <div className="skeleton-bar" style={{ width: "52%" }} />
              </div>
            )}
            {seriesQuery.isError && (
              <div className="state" role="alert">
                <p className="state-message">Could not load the series — try again.</p>
                <button type="button" className="btn" onClick={() => void seriesQuery.refetch()}>
                  Retry
                </button>
              </div>
            )}
            {seriesQuery.data !== undefined && (
              <svg
                viewBox={`0 0 ${CHART_W} ${CHART_H}`}
                role="img"
                aria-label={`${metric} over ${rangeDays} days, gaps visible`}
              >
                {segments.length === 0 ? (
                  <text x={CHART_W / 2} y={CHART_H / 2} textAnchor="middle" fill="currentColor">
                    no measured days in this window
                  </text>
                ) : (
                  segments.map((points, index) => (
                    <polyline
                      key={index}
                      points={points}
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.6"
                    />
                  ))
                )}
                {(context?.timezone_changes ?? []).map((change) => {
                  const x =
                    CHART_PAD +
                    ((Date.parse(`${change.date}T00:00:00Z`) - Date.parse(`${fromDate}T00:00:00Z`)) /
                      Math.max(Date.parse(`${toDate}T00:00:00Z`) - Date.parse(`${fromDate}T00:00:00Z`), 1)) *
                    (CHART_W - 2 * CHART_PAD);
                  return (
                    <line
                      key={change.date}
                      x1={x}
                      x2={x}
                      y1={CHART_PAD}
                      y2={CHART_H - CHART_PAD}
                      stroke="currentColor"
                      strokeDasharray="2 4"
                      strokeWidth="0.8"
                      opacity="0.5"
                    />
                  );
                })}
              </svg>
            )}

            {context !== undefined && (
              <dl className="stat-lines">
                <div className="stat-line">
                  <dt>Device boundaries</dt>
                  <dd>
                    {context.devices
                      .map((device) => `${device.name} (${device.active_from} → ${device.active_to ?? "now"})`)
                      .join(" · ") || "—"}
                  </dd>
                </div>
                <div className="stat-line">
                  <dt>Algorithm versions</dt>
                  <dd>
                    {context.algorithms
                      .filter((algorithm) => algorithm.first_seen !== null)
                      .map((algorithm) => `${algorithm.name} @ ${algorithm.first_seen}`)
                      .join(" · ") || "registry present, none seen in window"}
                  </dd>
                </div>
                <div className="stat-line">
                  <dt>Timezone changes</dt>
                  <dd>
                    {(context.timezone_changes ?? [])
                      .map((change) => `${change.date} → ${change.timezone}`)
                      .join(" · ") || "—"}
                  </dd>
                </div>
              </dl>
            )}
          </section>

          <section className="card" aria-label="Annotations">
            <header className="card-header">
              <h3>Annotations</h3>
              <p className="card-meta">your narrative — never system facts</p>
            </header>
            <form
              className="pref-row"
              onSubmit={(event) => {
                event.preventDefault();
                if (title.trim() !== "") annotate.mutate();
              }}
            >
              <label htmlFor="annotation-title">Note</label>
              <input
                id="annotation-title"
                type="text"
                maxLength={200}
                placeholder="e.g. started zone-2 block"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
              />
              <input
                type="date"
                aria-label="Anchor date (defaults to today)"
                value={anchor}
                onChange={(event) => setAnchor(event.target.value)}
              />
              <button type="submit" className="btn" disabled={annotate.isPending}>
                Pin
              </button>
            </form>
            <ul className="missing-list">
              {(annotationsQuery.data ?? []).map((annotation) => (
                <li key={annotation.id}>
                  <span className="num">{annotation.date_from}</span> · {annotation.title}
                  <button
                    type="button"
                    className="btn"
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(annotation.id)}
                    aria-label={`Delete annotation ${annotation.title}`}
                  >
                    ✕
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}
    </main>
  );
}
