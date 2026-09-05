"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { fetchCorrelationMatrix } from "../lib/api";
import { useSession } from "../lib/auth";
import type { CorrelationPairRow } from "../lib/api";

/**
 * Correlations (M10, spec §81-82, §203): a quiet scientific table, not a
 * dashboard widget. Rows are co-movement in the user's own data sorted by
 * |r|; the causal-language note is the card's persistent footer — a
 * coefficient is never shown without its framing (spec §82).
 */

/** Window options; 90 is the matrix default (spec §203 curation). */
const RANGE_OPTIONS = [30, 90, 180] as const;
const DEFAULT_RANGE = 90;

/** More than this many rows and the table stops being readable; the API
 * already sorts by |r|, so the strongest co-movements stay visible. */
const MAX_ROWS = 12;

function formatR(value: number): string {
  return (value >= 0 ? "+" : "") + value.toFixed(2);
}

export default function CorrelationsCard() {
  const { ready, token } = useSession();
  const [range, setRange] = useState<number>(DEFAULT_RANGE);

  const query = useQuery({
    queryKey: ["correlations", "matrix", { days: range }],
    queryFn: () => fetchCorrelationMatrix(range),
    enabled: ready && token !== null,
    staleTime: 300_000,
    refetchInterval: 600_000,
  });

  const data = query.data;

  /** |r| descending regardless of wire ordering; ties keep pair order. */
  const rows = useMemo(() => {
    if (data === undefined) return [] as CorrelationPairRow[];
    return [...data.pairs].sort((a, b) => Math.abs(b.r) - Math.abs(a.r));
  }, [data]);

  const errorReason =
    query.error instanceof Error ? query.error.message : "request failed";

  return (
    <section className="card" aria-label="Correlations">
      <header className="card-header">
        <h2>Correlations</h2>
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
        <div className="skeleton" role="status" aria-label="Loading correlations">
          <div className="skeleton-bar" style={{ width: "44%" }} />
          <div className="skeleton-bar" style={{ width: "72%" }} />
          <div className="skeleton-bar" style={{ width: "58%" }} />
          <div className="skeleton-bar" style={{ width: "66%" }} />
        </div>
      )}

      {ready && token === null && (
        <div className="state">
          <p className="state-message">
            Sign in to see your correlations — co-movement in your own data
            answers the owner&apos;s session only.
          </p>
          <Link href="/login/" className="btn">
            Sign in
          </Link>
        </div>
      )}

      {ready && token !== null && query.isPending && (
        <div className="skeleton" role="status" aria-label="Loading correlations">
          <div className="skeleton-bar" style={{ width: "44%" }} />
          <div className="skeleton-bar" style={{ width: "72%" }} />
          <div className="skeleton-bar" style={{ width: "58%" }} />
          <div className="skeleton-bar" style={{ width: "66%" }} />
        </div>
      )}

      {ready && token !== null && query.isError && (
        <div className="state" role="alert">
          <p className="state-message">
            Could not load correlations — {errorReason}. The API may be
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

      {data !== undefined && rows.length === 0 && (
        <div className="state">
          <p className="state-message">
            No correlations yet — a pair needs at least 14 shared days before
            a coefficient exists. Days with heart-rate samples or vendor daily
            scores build the overlap.
          </p>
        </div>
      )}

      {data !== undefined && rows.length > 0 && (
        <>
          <table className="corr-table">
            <thead>
              <tr>
                <th scope="col">pair</th>
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
              {rows.slice(0, MAX_ROWS).map((row) => (
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

          <p className="caveats num">
            {rows.length > MAX_ROWS
              ? `top ${MAX_ROWS} of ${rows.length} pairs by |r| · `
              : `${rows.length} pair${rows.length === 1 ? "" : "s"} · `}
            {data.method} · {data.nTests} tests · α&#8203;={data.bonferroniAlpha.toFixed(5)} (Bonferroni)
            {rows.some((row) => row.significant) ? " · * significant" : ""}
          </p>

          {data.skipped.length > 0 && (
            <details className="corr-skipped">
              <summary>
                insufficient data — {data.skipped.length} pair
                {data.skipped.length === 1 ? "" : "s"} skipped
              </summary>
              <ul>
                {data.skipped.map((row) => (
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
        </>
      )}

      {data !== undefined && (
        <p className="corr-note">{data.note}</p>
      )}
    </section>
  );
}
