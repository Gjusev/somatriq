"use client";

import { useEffect, useState } from "react";

type ApiStatus = "checking" | "reachable" | "unreachable";

/**
 * M0 bootstrap placeholder. Deliberately sober: no fake dashboard, no invented
 * numbers (spec §114 comes at M6 with the design system). This page only
 * proves the single-origin topology: static shell at `/`, API at `/api`.
 */
export default function HomePage() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>("checking");

  useEffect(() => {
    fetch("/api/health")
      .then((res) => setApiStatus(res.ok ? "reachable" : "unreachable"))
      .catch(() => setApiStatus("unreachable"));
  }, []);

  return (
    <main className="shell">
      <header className="masthead">
        <p className="eyebrow">Personal biometric intelligence</p>
        <h1>Somatriq</h1>
      </header>

      <section className="status" aria-live="polite">
        <h2 id="server-status">Server status</h2>
        <p>
          API at <code>/api</code>:{" "}
          <strong className={`status-${apiStatus}`}>
            {apiStatus === "checking" && "checking…"}
            {apiStatus === "reachable" && "reachable"}
            {apiStatus === "unreachable" && "unreachable"}
          </strong>
        </p>
        <p className="hint">
          Milestone M0 — repository bootstrap. The first real surface (Today)
          arrives with M6, after the design system is defined.
        </p>
      </section>
    </main>
  );
}
