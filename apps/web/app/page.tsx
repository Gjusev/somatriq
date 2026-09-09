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
 * Group the home feed by user intent rather than implementation milestone:
 * current state, longitudinal patterns, deliberate interventions, then
 * sources and ownership. Each card retains its own loading/empty/error cycle.
 */
export default function HomePage() {
  return (
    <main className="shell">
      <SiteHeader />
      <header className="dashboard-intro">
        <div>
          <p className="eyebrow">Personal evidence system</p>
          <h1>Your data, in context.</h1>
        </div>
        <p className="dashboard-intro-copy">
          Today first. Longer patterns and deliberate interventions follow,
          with coverage, provenance and uncertainty kept visible.
        </p>
      </header>

      <div className="dashboard">
        <section className="dashboard-section" id="today" aria-labelledby="section-today">
          <header className="dashboard-section-header">
            <p className="section-index num">01</p>
            <h2 className="dashboard-section-title" id="section-today">Daily state</h2>
            <p>Recovery and the sensor stream behind it, presented together.</p>
          </header>
          <div className="section-grid section-grid-primary">
            <TodayCard />
            <HeartRateCard />
          </div>
        </section>

        <section className="dashboard-section" id="patterns" aria-labelledby="section-patterns">
          <header className="dashboard-section-header">
            <p className="section-index num">02</p>
            <h2 className="dashboard-section-title" id="section-patterns">Patterns over time</h2>
            <p>Daily baselines and associations, without causal shortcuts.</p>
          </header>
          <div className="section-grid section-grid-balanced">
            <DailyCard />
            <CorrelationsCard />
          </div>
        </section>

        <section className="dashboard-section" id="interventions" aria-labelledby="section-interventions">
          <header className="dashboard-section-header">
            <p className="section-index num">03</p>
            <h2 className="dashboard-section-title" id="section-interventions">Interventions</h2>
            <p>Training load and N-of-1 experiments share one evidence loop.</p>
          </header>
          <div className="section-grid section-grid-balanced">
            <TrainingCard />
            <ExperimentsCard />
          </div>
        </section>

        <section className="dashboard-section" id="ownership" aria-labelledby="section-data">
          <header className="dashboard-section-header">
            <p className="section-index num">04</p>
            <h2 className="dashboard-section-title" id="section-data">Sources &amp; ownership</h2>
            <p>Pair collectors, inspect access and move your data freely.</p>
          </header>
          <div className="section-grid section-grid-ownership">
            <DevicesCard />
            <DataCard />
          </div>
        </section>
      </div>
    </main>
  );
}
