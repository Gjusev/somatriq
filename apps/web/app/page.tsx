"use client";

import TodayCard from "./components/today-card";
import HeartRateCard from "./components/heart-rate-card";
import DailyCard from "./components/daily-card";
import CorrelationsCard from "./components/correlations-card";
import ExperimentsCard from "./components/experiments-card";
import TrainingCard from "./components/training-card";
import DevicesCard from "./components/devices-card";
import DataCard from "./components/data-card";
import SiteHeader from "./components/site-header";

/**
 * The home feed. The Today card (M6) anchors the day: recovery, last night's
 * summaries and freshness, stated honestly — no invented numbers (spec §76).
 * Heart-rate context follows; correlations (M10) sit below the daily summary
 * as co-movement, never causation; experiments (M11) are the one surface
 * allowed to speak causally — a baseline, one change, an honest read.
 * Strength training (M12) adds the muscular-load layer — sessions, tonnage,
 * and the §80 training-vs-next-day-recovery response, correlational like
 * the matrix. The Data card (M13) closes the loop on ownership — CSV import
 * preview-first (§129) and full exports (§130), login-gated like devices.
 * Every data card is login-gated (spec §122): health reads answer the
 * owner's session only. The header carries the API-health dot and the theme
 * picker (design pass v2); the visible masthead is gone — the dashboard
 * starts at Today.
 */
export default function HomePage() {
  return (
    <main className="shell">
      <SiteHeader />
      <h1 className="sr-only">Somatriq — personal biometric intelligence</h1>

      {/*
       * Asymmetric dashboard (design pass 2026-09-05): the main column
       * carries the day and the science (Today, heart rate, daily summary,
       * correlations, training response); the rail carries management
       * (devices, experiments, data ownership). Single column under 64rem.
       */}
      <div className="dashboard">
        <div className="dashboard-main">
          <TodayCard />
          <HeartRateCard />
          <DailyCard />
          <CorrelationsCard />
          <TrainingCard />
        </div>
        <aside className="dashboard-rail">
          <DevicesCard />
          <ExperimentsCard />
          <DataCard />
        </aside>
      </div>
    </main>
  );
}
