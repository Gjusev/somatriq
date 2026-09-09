"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import ReactECharts from "echarts-for-react";
import type {
  EChartsOption,
  TooltipComponentFormatterCallbackParams,
} from "echarts";
import { fetchDailySummary } from "../lib/api";
import { useSession } from "../lib/auth";
import { readChartPalette, useColorSchemeVersion } from "../lib/chart-palette";
import type { DailyHeartSummary } from "../lib/api";

const MONO = 'ui-monospace, "Cascadia Mono", monospace';

/** Quiet range selector; 14 is the default view (spec §169 retention hint). */
const RANGE_OPTIONS = [7, 14, 30] as const;
const DEFAULT_RANGE = 14;

/** A day needs ~2.5 h of non-empty 5-minute buckets before a resting
 * estimate exists at all (RHR_MIN_BUCKETS); the copy stays qualitative. */
const NO_ESTIMATE_HINT =
  "A resting estimate needs a quieter, better-covered day to appear.";


const ISO_DAY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;

/**
 * Format a payload day from its own components. The wire date is already the
 * local calendar day in the day's effective timezone (ADR 0017), so it must
 * never round-trip through the browser timezone — Date.parse("2026-09-04")
 * is UTC midnight and would shift the label a day west of Greenwich.
 */
function formatDayLabel(date: string): string {
  const match = ISO_DAY_PATTERN.exec(date);
  if (match === null) return date;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (!Number.isInteger(year) || !Number.isInteger(month) || !Number.isInteger(day)) {
    return date;
  }
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

/** Quiet caveat shown whenever the focus day is not "good" (spec §174 quality
 * indication: never a confident number without its coverage story). */
function qualityCaveat(day: DailyHeartSummary): string {
  switch (day.dataQuality) {
    case "fair":
      return `partial day — coverage ${Math.round(day.coverageRatio * 100)}%`;
    case "poor":
      return `sparse coverage (${Math.round(day.coverageRatio * 100)}%) — treat with care`;
    case "insufficient":
      return "insufficient data for a resting estimate";
    case "good":
      return "";
  }
}

function formatBpm(value: number | null): string {
  return value === null ? "—" : String(Math.round(value));
}

function buildChartOption(
  palette: { ink: string; muted: string; paper: string; accent: string },
  days: DailyHeartSummary[],
  yMin: number,
  yMax: number,
): EChartsOption {
  const formatTooltip = (params: TooltipComponentFormatterCallbackParams): string => {
    const first = Array.isArray(params) ? params[0] : params;
    if (first === undefined || typeof first.dataIndex !== "number") {
      return "";
    }
    const day = days[first.dataIndex];
    if (day === undefined) {
      return "";
    }
    const resting =
      day.restingHr === null
        ? "no resting estimate"
        : `resting ${Math.round(day.restingHr)} bpm`;
    return `${formatDayLabel(day.date)}<br/>${resting}<br/>coverage ${Math.round(
      day.coverageRatio * 100,
    )}% · ${day.dataQuality}`;
  };

  return {
    // Spec §177: charts are not animated gratuitously. Also keeps
    // prefers-reduced-motion respected by construction.
    animation: false,
    grid: { left: 8, right: 8, top: 20, bottom: 0, containLabel: true },
    xAxis: {
      type: "category",
      data: days.map((day) => formatDayLabel(day.date)),
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
      min: yMin,
      max: yMax,
      name: "bpm",
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
      formatter: formatTooltip,
    },
    series: [
      {
        name: "Resting HR",
        type: "line",
        data: days.map((day) => day.restingHr),
        // Daily points are sparse; symbols keep isolated days visible.
        showSymbol: true,
        symbol: "circle",
        symbolSize: 4,
        connectNulls: false,
        lineStyle: { width: 1.5, color: palette.accent },
        itemStyle: { color: palette.accent },
        areaStyle: { color: palette.accent, opacity: 0.1 },
      },
    ],
  };
}

export default function DailyCard() {
  const { ready, token } = useSession();
  const [range, setRange] = useState<number>(DEFAULT_RANGE);
  const schemeVersion = useColorSchemeVersion();
  const palette = useMemo(readChartPalette, [schemeVersion]);

  const query = useQuery({
    queryKey: ["metrics", "daily", { days: range }],
    queryFn: () => fetchDailySummary(range),
    enabled: ready && token !== null,
    staleTime: 60_000,
    refetchInterval: 300_000,
  });

  const data = query.data;

  /** Oldest-first regardless of wire ordering (ISO days sort lexically). */
  const days = useMemo(() => {
    if (data === undefined) return [] as DailyHeartSummary[];
    return [...data.days].sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  }, [data]);

  const hasSamples = days.some((day) => day.sampleCount > 0);

  /**
   * The headline describes the most recent day that actually has a resting
   * estimate, labelled with its date — today typically renders "—" while its
   * coverage is still accumulating, which is the honest presentation.
   */
  const focus = useMemo(() => {
    for (let i = days.length - 1; i >= 0; i -= 1) {
      const day = days[i];
      if (day !== undefined && day.restingHr !== null) return day;
    }
    return days.length > 0 ? days[days.length - 1] : undefined;
  }, [days]);

  const { yMin, yMax, daysWithEstimate, rhrMin, rhrMax } = useMemo(() => {
    const present = days
      .map((day) => day.restingHr)
      .filter((value): value is number => value !== null);
    if (present.length === 0) {
      return { yMin: 0, yMax: 100, daysWithEstimate: 0, rhrMin: 0, rhrMax: 0 };
    }
    const min = Math.min(...present);
    const max = Math.max(...present);
    const pad = Math.max(4, Math.round((max - min) * 0.15));
    return {
      yMin: Math.max(0, Math.floor(min - pad)),
      yMax: Math.ceil(max + pad),
      daysWithEstimate: present.length,
      rhrMin: Math.round(min),
      rhrMax: Math.round(max),
    };
  }, [days]);

  const chartOption = useMemo(
    () => buildChartOption(palette, days, yMin, yMax),
    [palette, days, yMin, yMax],
  );

  const errorReason =
    query.error instanceof Error ? query.error.message : "request failed";

  const caveat = focus !== undefined ? qualityCaveat(focus) : "";
  const coveragePercent =
    focus === undefined ? 0 : Math.round(focus.coverageRatio * 100);

  return (
    <section className="card" aria-label="Daily heart summary">
      <header className="card-header">
        <h3>Resting heart rate — daily</h3>
        <div className="range-group" role="group" aria-label="Day range">
          {RANGE_OPTIONS.map((option) => (
            <button
              key={option}
              type="button"
              className="range-btn num"
              aria-pressed={option === range}
              onClick={() => setRange(option)}
            >
              {option}d
            </button>
          ))}
        </div>
      </header>

      {!ready && (
        <div className="skeleton" role="status" aria-label="Loading daily summary">
          <div className="skeleton-bar" style={{ width: "38%" }} />
          <div className="skeleton-chart" />
          <div className="skeleton-bar" style={{ width: "56%" }} />
        </div>
      )}

      {ready && token === null && (
        <div className="state">
          <p className="state-message">
            Sign in to see your daily resting heart rate — health reads answer
            the owner&apos;s session only.
          </p>
          <Link href="/login/" className="btn">
            Sign in
          </Link>
        </div>
      )}

      {ready && token !== null && query.isPending && (
        <div className="skeleton" role="status" aria-label="Loading daily summary">
          <div className="skeleton-bar" style={{ width: "38%" }} />
          <div className="skeleton-chart" />
          <div className="skeleton-bar" style={{ width: "56%" }} />
        </div>
      )}

      {ready && token !== null && query.isError && (
        <div className="state" role="alert">
          <p className="state-message">
            Could not load the daily summary — {errorReason}. The API may be
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

      {data !== undefined && !hasSamples && (
        <div className="state">
          <p className="state-message">
            No daily summaries yet — heart-rate samples need to cover enough of
            a day before a resting estimate appears. Send a batch to{" "}
            <code>/api/v1/ingest/batches</code> to start.
          </p>
        </div>
      )}

      {data !== undefined && hasSamples && focus !== undefined && (
        <>
          <div className="stat-primary">
            <div>
              <p className="stat-value num">{formatBpm(focus.restingHr)}</p>
              <p className="stat-caption">
                bpm resting · {formatDayLabel(focus.date)} · {focus.timezone}
              </p>
            </div>
            <div className="stat-quality">
              <span className="badge">{focus.dataQuality}</span>
              <span className="num">coverage {coveragePercent}%</span>
            </div>
          </div>

          {caveat !== "" && <p className="caveats">{caveat}</p>}

          <dl className="stat-row">
            <div className="stat-cell">
              <dt>min</dt>
              <dd className="num">{formatBpm(focus.hrMin)}</dd>
            </div>
            <div className="stat-cell">
              <dt>mean</dt>
              <dd className="num">{formatBpm(focus.hrMean)}</dd>
            </div>
            <div className="stat-cell">
              <dt>max</dt>
              <dd className="num">{formatBpm(focus.hrMax)}</dd>
            </div>
            <div className="stat-cell">
              <dt>samples</dt>
              <dd className="num">{focus.sampleCount.toLocaleString()}</dd>
            </div>
          </dl>

          {daysWithEstimate > 0 ? (
            <figure className="chart-figure">
              <div
                className="chart-frame"
                role="img"
                aria-label={`Resting heart rate by day for the last ${days.length} days: ${daysWithEstimate} of ${days.length} days with a resting estimate, ranging ${rhrMin} to ${rhrMax} bpm.`}
              >
                <ReactECharts
                  option={chartOption}
                  notMerge={true}
                  lazyUpdate={true}
                  style={{ width: "100%", height: "100%" }}
                />
              </div>
            </figure>
          ) : (
            <p className="caveats">{NO_ESTIMATE_HINT}</p>
          )}

          {focus.algorithmVersion !== null && (
            <p className="caveats num">resting estimate: {focus.algorithmVersion}</p>
          )}
        </>
      )}
    </section>
  );
}
