"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import ReactECharts from "echarts-for-react";
import type {
  EChartsOption,
  TooltipComponentFormatterCallbackParams,
} from "echarts";

const HEART_RATE_URL = "/api/v1/metrics/heart_rate?last_hours=24&bucket=5m";

/** Requested bucket size. A stretch longer than two buckets renders as a gap. */
const BUCKET_MS = 5 * 60_000;
const GAP_MS = 2 * BUCKET_MS;

const MONO = 'ui-monospace, "Cascadia Mono", monospace';

type HeartRatePoint = {
  ts: string;
  bpm: number;
  sampleCount: number;
};

type HeartRatePayload = {
  unit: string;
  points: HeartRatePoint[];
  count: number;
  /** Percent 0-100, or null when the API omits it. */
  coverage: number | null;
  caveats: string[];
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * Minimal validation at the fetch boundary: the wire shape is never trusted
 * (ADR 0014). Anything malformed throws and the card enters its error state.
 */
function parseHeartRateResponse(raw: unknown): HeartRatePayload {
  if (!isRecord(raw) || !Array.isArray(raw.points)) {
    throw new Error("unexpected response shape");
  }

  const points: HeartRatePoint[] = [];
  for (const entry of raw.points) {
    if (
      !isRecord(entry) ||
      typeof entry.ts !== "string" ||
      Number.isNaN(Date.parse(entry.ts)) ||
      typeof entry.bpm !== "number" ||
      !Number.isFinite(entry.bpm)
    ) {
      throw new Error("unexpected response shape");
    }
    points.push({
      ts: entry.ts,
      bpm: entry.bpm,
      sampleCount:
        typeof entry.sample_count === "number" &&
        Number.isFinite(entry.sample_count)
          ? entry.sample_count
          : 0,
    });
  }

  let coverage: number | null = null;
  if (typeof raw.coverage === "number" && Number.isFinite(raw.coverage)) {
    const percent = raw.coverage <= 1 ? raw.coverage * 100 : raw.coverage;
    coverage = Math.min(100, Math.max(0, Math.round(percent)));
  }

  return {
    unit: typeof raw.unit === "string" ? raw.unit : "bpm",
    points,
    count: typeof raw.count === "number" ? raw.count : points.length,
    coverage,
    caveats: Array.isArray(raw.caveats)
      ? raw.caveats.filter((caveat): caveat is string => typeof caveat === "string")
      : [],
  };
}

async function fetchHeartRate(): Promise<HeartRatePayload> {
  const res = await fetch(HEART_RATE_URL, { credentials: "same-origin" });
  if (!res.ok) {
    throw new Error(`API responded with ${res.status}`);
  }
  return parseHeartRateResponse(await res.json());
}

/** Bump a counter when the color scheme flips so the chart re-reads the CSS tokens. */
function useColorSchemeVersion(): number {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setVersion((n) => n + 1);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  return version;
}

/** Read palette values from the existing CSS tokens so light/dark stay in sync. */
function readPalette(): { ink: string; muted: string; paper: string } {
  if (typeof window === "undefined") {
    // Static prerender: echarts never renders on the server, fallbacks only.
    return { ink: "#18181b", muted: "#71717a", paper: "#fafafa" };
  }
  const styles = getComputedStyle(document.documentElement);
  return {
    ink: styles.getPropertyValue("--ink").trim() || "#18181b",
    muted: styles.getPropertyValue("--muted").trim() || "#71717a",
    paper: styles.getPropertyValue("--paper").trim() || "#fafafa",
  };
}

/** [timestamp, bpm] pairs with null sentinels marking missing-data gaps (§174). */
function toSeriesData(points: HeartRatePoint[]): [number, number | null][] {
  const data: [number, number | null][] = [];
  let previous: number | null = null;
  for (const point of points) {
    const ts = Date.parse(point.ts);
    if (previous !== null && ts - previous > GAP_MS) {
      data.push([previous + BUCKET_MS, null]);
      data.push([ts - BUCKET_MS, null]);
    }
    data.push([ts, point.bpm]);
    previous = ts;
  }
  return data;
}

function formatTooltip(params: TooltipComponentFormatterCallbackParams): string {
  const first = Array.isArray(params) ? params[0] : params;
  if (first === undefined || !Array.isArray(first.value)) {
    return "";
  }
  const [ts, bpm] = first.value;
  if (typeof ts !== "number" || typeof bpm !== "number") {
    return "";
  }
  const when = new Date(ts).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
  return `${when}<br/>${Math.round(bpm)} bpm`;
}

function buildChartOption(
  palette: { ink: string; muted: string; paper: string },
  seriesData: [number, number | null][],
  yMin: number,
  yMax: number,
): EChartsOption {
  return {
    // Spec §177: charts are not animated gratuitously. Also keeps
    // prefers-reduced-motion respected by construction.
    animation: false,
    grid: { left: 8, right: 8, top: 20, bottom: 0, containLabel: true },
    xAxis: {
      type: "time",
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
        name: "Heart rate",
        type: "line",
        data: seriesData,
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 1.5, color: palette.ink },
        itemStyle: { color: palette.ink },
        areaStyle: { color: palette.ink, opacity: 0.08 },
      },
    ],
  };
}

export default function HeartRateCard() {
  const schemeVersion = useColorSchemeVersion();
  const palette = useMemo(readPalette, [schemeVersion]);

  const query = useQuery({
    queryKey: ["metrics", "heart_rate", { lastHours: 24, bucket: "5m" }],
    queryFn: fetchHeartRate,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const data = query.data;
  const points = data?.points ?? [];

  const { seriesData, yMin, yMax, minBpm, maxBpm, samples } = useMemo(() => {
    if (points.length === 0) {
      return {
        seriesData: [] as [number, number | null][],
        yMin: 0,
        yMax: 100,
        minBpm: 0,
        maxBpm: 0,
        samples: 0,
      };
    }
    const bpms = points.map((point) => point.bpm);
    const min = Math.min(...bpms);
    const max = Math.max(...bpms);
    const pad = Math.max(4, Math.round((max - min) * 0.15));
    return {
      seriesData: toSeriesData(points),
      yMin: Math.max(0, Math.floor(min - pad)),
      yMax: Math.ceil(max + pad),
      minBpm: Math.round(min),
      maxBpm: Math.round(max),
      samples: points.reduce((sum, point) => sum + point.sampleCount, 0),
    };
  }, [points]);

  const option = useMemo(
    () => buildChartOption(palette, seriesData, yMin, yMax),
    [palette, seriesData, yMin, yMax],
  );

  const errorReason =
    query.error instanceof Error ? query.error.message : "request failed";

  return (
    <section className="card" aria-label="Heart rate, last 24 hours">
      <header className="card-header">
        <h2>Heart rate — last 24h</h2>
        {data !== undefined && points.length > 0 && (
          <p className="card-meta">
            {data.coverage !== null && (
              <>
                <span className="num">coverage {data.coverage}%</span>
                <span aria-hidden="true"> · </span>
              </>
            )}
            <span className="num">
              {(samples > 0 ? samples : data.count).toLocaleString()} samples
            </span>
          </p>
        )}
      </header>

      {query.isPending && (
        <div className="skeleton" role="status" aria-label="Loading heart-rate data">
          <div className="skeleton-bar" style={{ width: "38%" }} />
          <div className="skeleton-chart" />
          <div className="skeleton-bar" style={{ width: "56%" }} />
        </div>
      )}

      {query.isError && (
        <div className="state" role="alert">
          <p className="state-message">
            Could not load heart-rate data — {errorReason}. The API may be
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

      {data !== undefined && points.length === 0 && (
        <div className="state">
          <p className="state-message">
            No heart-rate data yet — send a synthetic batch to{" "}
            <code>/api/v1/ingest/batches</code>
          </p>
        </div>
      )}

      {data !== undefined && points.length > 0 && (
        <figure className="chart-figure">
          <div
            className="chart-frame"
            role="img"
            aria-label={`Heart rate line chart for the last 24 hours: ${data.count} points, ranging ${minBpm} to ${maxBpm} bpm.`}
          >
            <ReactECharts
              option={option}
              notMerge={true}
              lazyUpdate={true}
              style={{ width: "100%", height: "100%" }}
            />
          </div>
        </figure>
      )}

      {data !== undefined && data.caveats.length > 0 && (
        <p className="caveats">{data.caveats.join(" ")}</p>
      )}
    </section>
  );
}
