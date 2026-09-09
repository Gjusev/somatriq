"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { downloadHealthReport, fetchHealthMonitor } from "../lib/api";
import { useSession } from "../lib/auth";
import SiteHeader from "../components/site-header";

/**
 * The private vitals monitor (Block 3, grill P14): each vital against the
 * personal baseline with coverage and provenance — directional words
 * (elevated/reduced/within), never clinical ones — plus the shareable,
 * deterministic PDF report.
 */

const PERIODS: (30 | 90 | 180)[] = [30, 90, 180];

const STATUS_COPY: Record<string, string> = {
  within: "in line with baseline",
  elevated: "above baseline",
  reduced: "below baseline",
  building: "baseline building",
  insufficient: "no data in period",
};

export default function HealthMonitorPage() {
  const { ready, token } = useSession();
  const [days, setDays] = useState<30 | 90 | 180>(30);
  const signedIn = ready && token !== null;

  const query = useQuery({
    queryKey: ["health-monitor", days],
    queryFn: () => fetchHealthMonitor(days),
    enabled: signedIn,
    staleTime: 300_000,
  });

  return (
    <main className="shell">
      <SiteHeader />
      <header className="dashboard-intro">
        <div>
          <p className="eyebrow">Private monitoring</p>
          <h1>Health Monitor.</h1>
        </div>
        <p className="dashboard-intro-copy">
          Slow-moving vitals against your personal baseline — coverage and
          provenance visible, deviations directional, never a diagnosis.
        </p>
      </header>

      {!signedIn ? (
        <div className="card">
          <div className="state">
            <p className="state-message">Sign in to see your vitals.</p>
            <Link href="/login/" className="btn">Sign in</Link>
          </div>
        </div>
      ) : (
        <div className="dashboard">
          <section className="card" aria-label="Health Monitor vitals">
            <header className="card-header">
              <h3>Vitals vs baseline</h3>
              <div className="pref-row">
                {PERIODS.map((period) => (
                  <button
                    key={period}
                    type="button"
                    className="btn"
                    aria-pressed={days === period}
                    onClick={() => setDays(period)}
                  >
                    {period}d
                  </button>
                ))}
                <button
                  type="button"
                  className="btn"
                  onClick={() => void downloadHealthReport(days === 90 ? 30 : (days as 30 | 180))}
                >
                  Export PDF
                </button>
              </div>
            </header>

            {query.isPending && (
              <div className="skeleton" role="status" aria-label="Loading vitals">
                <div className="skeleton-bar" style={{ width: "60%" }} />
                <div className="skeleton-bar" style={{ width: "48%" }} />
              </div>
            )}
            {query.isError && (
              <div className="state" role="alert">
                <p className="state-message">Could not load vitals — try again.</p>
                <button type="button" className="btn" onClick={() => void query.refetch()}>
                  Retry
                </button>
              </div>
            )}
            {query.data !== undefined && (
              <>
                <dl className="stat-lines">
                  {query.data.vitals.map((vital) => (
                    <div className="stat-line" key={vital.vital}>
                      <dt>{vital.label}</dt>
                      <dd>
                        <span className="badge">{vital.status}</span>{" "}
                        {STATUS_COPY[vital.status] ?? vital.status}
                      </dd>
                      <dd className="stat-line-note num">
                        median {vital.period_median?.toFixed(1) ?? "—"} vs baseline{" "}
                        {vital.baseline_median?.toFixed(1) ?? "—"} · z{" "}
                        {vital.robust_z !== null ? vital.robust_z.toFixed(2) : "—"} ·{" "}
                        {Math.round(vital.coverage * 100)}% coverage · n {vital.n_days} ·{" "}
                        {vital.source}
                      </dd>
                    </div>
                  ))}
                </dl>
                {query.data.caveats.length > 0 && (
                  <p className="caveats">{query.data.caveats.join(" ")}</p>
                )}
                <p className="caveats">{query.data.disclaimer}</p>
              </>
            )}
          </section>
        </div>
      )}
    </main>
  );
}
