"use client";

import { useEffect, useState } from "react";
import TodayCard from "./components/today-card";
import HeartRateCard from "./components/heart-rate-card";
import DailyCard from "./components/daily-card";
import DevicesCard from "./components/devices-card";
import SiteHeader from "./components/site-header";

type ApiStatus = "checking" | "reachable" | "unreachable";

/**
 * The home feed. The Today card (M6) anchors the day: recovery, last night's
 * summaries and freshness, stated honestly — no invented numbers (spec §76).
 * Heart-rate context follows; the metric endpoints are unauthenticated today.
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
      <SiteHeader />
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
          The Today screen anchors the day — recovery, last night, freshness.
          Heart-rate context follows below.
        </p>
      </section>

      <TodayCard />
      <HeartRateCard />
      <DailyCard />
      <DevicesCard />
    </main>
  );
}
