"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  EXPERIMENT_METRIC_OPTIONS,
  createExperiment,
  fetchExperiments,
} from "../lib/api";
import type {
  Experiment,
  ExperimentDirection,
  ExperimentDraft,
  ExperimentEvaluation,
} from "../lib/api";
import { useSession } from "../lib/auth";

/**
 * Experiments (M11, spec §83-86, §204): the N-of-1 layer. Baseline then
 * intervention, compliance first-class, and — only here — causal-adjacent
 * language (spec §82): an experiment is the one surface that changed one
 * thing on purpose. Even so, a completed experiment reads "consistent with
 * effect", never "proves"; the N-of-1 caveat is rendered with every
 * verdict. Login-gated like the devices card (writes need the account
 * JWT; the list is the same quiet scientific register as correlations).
 */

const DIRECTION_LABELS: Record<ExperimentDirection, string> = {
  increase: "increases",
  decrease: "decreases",
  any: "changes either way",
};

function formatSigned(value: number): string {
  return (value >= 0 ? "+" : "") + value.toFixed(2);
}

function formatP(value: number | null): string {
  if (value === null) return "—";
  return value < 0.0001 && value > 0 ? "<0.0001" : value.toFixed(4);
}

/** Overall compliance across both materialized phases. */
function overallCompliance(experiment: Experiment): { complied: number; total: number } {
  const complied =
    experiment.compliance.baseline.complied + experiment.compliance.intervention.complied;
  const total =
    experiment.compliance.baseline.total + experiment.compliance.intervention.total;
  return { complied, total };
}

/** Quiet hairline progress bar (tokens only, no gradient). */
function PhaseBar({ label, elapsed, total }: { label: string; elapsed: number; total: number }) {
  const fraction = total > 0 ? Math.min(elapsed / total, 1) : 0;
  return (
    <div className="exp-phase">
      <span className="exp-phase-label">
        {label} <span className="num">{elapsed}/{total}</span>
      </span>
      <span className="exp-bar" role="presentation">
        <span className="exp-bar-fill" style={{ width: `${Math.round(fraction * 100)}%` }} />
      </span>
    </div>
  );
}

function VerdictLine({ evaluation, direction }: {
  evaluation: ExperimentEvaluation;
  direction: ExperimentDirection;
}) {
  const matches =
    evaluation.verdict === "consistent with effect" &&
    (direction === "any" ||
      (direction === "increase" && (evaluation.meanDifference ?? 0) > 0) ||
      (direction === "decrease" && (evaluation.meanDifference ?? 0) < 0));
  const tone = evaluation.verdict === "inconclusive"
    ? "exp-verdict-neutral"
    : evaluation.verdict === "opposite of hypothesis" || !matches
      ? "exp-verdict-bad"
      : "exp-verdict-ok";
  const numbers: string[] = [];
  if (evaluation.cohensD !== null) numbers.push(`d ${formatSigned(evaluation.cohensD)}`);
  numbers.push(`p ${formatP(evaluation.pValue)}`);
  numbers.push(`n ${evaluation.nBaseline} vs ${evaluation.nIntervention}`);
  if (evaluation.excludedNoncomplied > 0) {
    numbers.push(`${evaluation.excludedNoncomplied} non-complied day${evaluation.excludedNoncomplied === 1 ? "" : "s"} excluded`);
  }
  return (
    <>
      <p className={`exp-verdict ${tone}`}>
        {evaluation.verdict} · {numbers.join(" · ")}
      </p>
      <p className="exp-caveat">{evaluation.caveat}</p>
    </>
  );
}

function ExperimentRow({ experiment }: { experiment: Experiment }) {
  const compliance = overallCompliance(experiment);
  const compliancePct = compliance.total > 0
    ? Math.round((compliance.complied / compliance.total) * 100)
    : 100;
  return (
    <li className="exp-row">
      <div className="exp-main">
        <p className="exp-name">
          {experiment.name}
          {experiment.status !== "running" && (
            <span className="badge">{experiment.status}</span>
          )}
        </p>
        <p className="exp-hypothesis">
          {experiment.hypothesis} — expects {experiment.metric}{" "}
          {DIRECTION_LABELS[experiment.direction]}
        </p>
        <div className="exp-phases">
          <PhaseBar
            label="baseline"
            elapsed={experiment.progress.baseline.elapsed}
            total={experiment.progress.baseline.total}
          />
          <PhaseBar
            label="intervention"
            elapsed={experiment.progress.intervention.elapsed}
            total={experiment.progress.intervention.total}
          />
        </div>
        <p className="exp-meta num">
          {experiment.intervention} · compliance {compliancePct}% ({compliance.complied}/
          {compliance.total} days)
        </p>
        {experiment.evaluation !== null && (
          <VerdictLine evaluation={experiment.evaluation} direction={experiment.direction} />
        )}
      </div>
    </li>
  );
}

const EMPTY_DRAFT: ExperimentDraft = {
  name: "",
  hypothesis: "",
  intervention: "",
  metric: "avg_hrv",
  direction: "increase",
};

function CreateForm() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<ExperimentDraft>(EMPTY_DRAFT);
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => createExperiment(draft),
    onSuccess: () => {
      setDraft(EMPTY_DRAFT);
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["experiments"] });
    },
    onError: (err: Error) => setError(err.message),
  });

  const set = <K extends keyof ExperimentDraft>(key: K, value: ExperimentDraft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    create.mutate();
  };

  return (
    <form className="exp-form" onSubmit={submit}>
      <div className="field">
        <label htmlFor="exp-name">Name</label>
        <input
          id="exp-name"
          value={draft.name}
          onChange={(event) => set("name", event.target.value)}
          maxLength={200}
          required
        />
      </div>
      <div className="field">
        <label htmlFor="exp-hypothesis">Hypothesis</label>
        <input
          id="exp-hypothesis"
          value={draft.hypothesis}
          onChange={(event) => set("hypothesis", event.target.value)}
          maxLength={2000}
          required
        />
        <p className="field-hint">e.g. avoiding caffeine after 14:00 improves my sleep</p>
      </div>
      <div className="field">
        <label htmlFor="exp-intervention">Intervention (what changes)</label>
        <input
          id="exp-intervention"
          value={draft.intervention}
          onChange={(event) => set("intervention", event.target.value)}
          maxLength={2000}
          required
        />
      </div>
      <div className="exp-form-row">
        <div className="field">
          <label htmlFor="exp-metric">Outcome metric</label>
          <select
            id="exp-metric"
            value={draft.metric}
            onChange={(event) => set("metric", event.target.value)}
          >
            {EXPERIMENT_METRIC_OPTIONS.map((metric) => (
              <option key={metric} value={metric}>
                {metric}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="exp-direction">Expected direction</label>
          <select
            id="exp-direction"
            value={draft.direction}
            onChange={(event) =>
              set("direction", event.target.value as ExperimentDirection)
            }
          >
            <option value="increase">increases</option>
            <option value="decrease">decreases</option>
            <option value="any">either way</option>
          </select>
        </div>
      </div>
      {error !== null && (
        <p className="form-error" role="alert">
          Could not start the experiment — {error}
        </p>
      )}
      <div className="form-actions">
        <button type="submit" className="btn" disabled={create.isPending}>
          {create.isPending ? "Starting…" : "Start experiment"}
        </button>
      </div>
      <p className="field-hint">
        Starts by observing the last 14 days as the baseline; the intervention begins
        tomorrow and runs 14 days. Check in from Telegram.
      </p>
    </form>
  );
}

export default function ExperimentsCard() {
  const { ready, token } = useSession();

  const query = useQuery({
    queryKey: ["experiments"],
    queryFn: fetchExperiments,
    enabled: ready && token !== null,
    staleTime: 60_000,
  });

  const experiments = query.data ?? [];

  return (
    <section className="card" id="experiments" aria-label="Experiments">
      <header className="card-header">
        <h3>Experiments</h3>
      </header>

      {!ready && (
        <div className="skeleton" role="status" aria-label="Loading experiments">
          <div className="skeleton-bar" style={{ width: "44%" }} />
          <div className="skeleton-bar" style={{ width: "30%" }} />
        </div>
      )}

      {ready && token === null && (
        <div className="state">
          <p className="state-message">
            Sign in to run N-of-1 experiments — a baseline, one change, and an
            honest read of what moved.
          </p>
          <Link href="/login/" className="btn">
            Sign in
          </Link>
        </div>
      )}

      {ready && token !== null && query.isError && (
        <div className="state" role="alert">
          <p className="state-message">
            Could not load experiments —{" "}
            {query.error instanceof Error ? query.error.message : "request failed"}.
          </p>
          <button type="button" className="btn" onClick={() => void query.refetch()}>
            Retry
          </button>
        </div>
      )}

      {ready && token !== null && query.isPending && (
        <div className="skeleton" role="status" aria-label="Loading experiments">
          <div className="skeleton-bar" style={{ width: "44%" }} />
          <div className="skeleton-bar" style={{ width: "58%" }} />
          <div className="skeleton-bar" style={{ width: "36%" }} />
        </div>
      )}

      {query.data !== undefined && experiments.length === 0 && (
        <div className="state">
          <p className="state-message">
            No experiments yet. Start one below — Somatriq observes a baseline
            first, then changes exactly one thing.
          </p>
        </div>
      )}

      {query.data !== undefined && experiments.length > 0 && (
        <ul className="exp-list">
          {experiments.map((experiment) => (
            <ExperimentRow key={experiment.id} experiment={experiment} />
          ))}
        </ul>
      )}

      {ready && token !== null && query.data !== undefined && <CreateForm />}
    </section>
  );
}
