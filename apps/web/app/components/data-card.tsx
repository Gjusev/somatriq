"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import { commitCsvImport, downloadExportFile, previewCsvImport } from "../lib/api";
import type { ImportCommitResult, ImportPreview } from "../lib/api";
import { useSession } from "../lib/auth";

/**
 * Data ownership (M13, spec §129-130): import a daily-metrics CSV through a
 * preview-first flow, and export everything as files. Preview shows the
 * exact §129 fields — date range, metrics, record count, duplicates and
 * validation warnings — before the commit button unlocks; commit replays
 * the previewed content through the idempotent ingest path and reports
 * inserted/duplicate counts. The three export links fetch with the account
 * JWT and hand the browser a blob: the token lives in localStorage and can
 * never ride an <a href>, so downloadExportFile (lib/api) clicks a
 * temporary object URL instead. Login-gated like the devices card; tokens
 * only, no new CSS.
 */

/** base64 of UTF-8 text, chunked past btoa's argument limits. */
function encodeBase64Utf8(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("could not read file"));
    reader.readAsText(file);
  });
}

const EXPORTS: { path: string; filename: string; label: string; hint: string }[] = [
  {
    path: "/api/v1/export/daily.json",
    filename: "somatriq-daily.json",
    label: "Daily JSON",
    hint: "features, observations, training, experiments, journal",
  },
  {
    path: "/api/v1/export/daily.csv",
    filename: "somatriq-daily.csv",
    label: "Daily CSV",
    hint: "flat long-form: date, metric, value, source",
  },
  {
    path: "/api/v1/export/raw-batches.json",
    filename: "somatriq-raw-batches.json",
    label: "Raw registry JSON",
    hint: "blob metadata + sha/path (blobs stay on the volume)",
  },
];

function PreviewSummary({ preview }: { preview: ImportPreview }) {
  const range =
    preview.dateRange === null
      ? "no dates"
      : `${preview.dateRange.firstDay} → ${preview.dateRange.lastDay}`;
  const hiddenWarnings = preview.validationWarningCount - preview.validationWarnings.length;
  return (
    <div>
      <table className="corr-table">
        <tbody>
          <tr>
            <th scope="row">date range</th>
            <td className="num">{range}</td>
          </tr>
          <tr>
            <th scope="row">records</th>
            <td className="num">{preview.recordCount}</td>
          </tr>
          <tr>
            <th scope="row">duplicates</th>
            <td className="num">
              {preview.duplicates.inFile} in file · {preview.duplicates.alreadyPresent} already
              present
            </td>
          </tr>
          {preview.metrics.length > 0 && (
            <tr>
              <th scope="row">metrics</th>
              <td>
                {preview.metrics.map((metric) => (
                  <span key={metric.metric}>
                    <span className="num">{metric.count}</span> {metric.metric}
                    {metric !== preview.metrics[preview.metrics.length - 1] && (
                      <span aria-hidden="true"> · </span>
                    )}
                  </span>
                ))}
              </td>
            </tr>
          )}
          {preview.skippedColumns.length > 0 && (
            <tr>
              <th scope="row">skipped columns</th>
              <td>{preview.skippedColumns.join(", ")}</td>
            </tr>
          )}
        </tbody>
      </table>
      {preview.validationWarnings.length > 0 && (
        <ul className="missing-list" aria-label="Validation warnings">
          {preview.validationWarnings.map((warning) => (
            <li key={`${warning.line}-${warning.reason}`}>
              line <span className="num">{warning.line}</span> — {warning.reason}
            </li>
          ))}
          {hiddenWarnings > 0 && (
            <li>
              <span className="num">+{hiddenWarnings}</span> more warning
              {hiddenWarnings === 1 ? "" : "s"} not shown
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

export default function DataCard() {
  const { ready, token } = useSession();

  const [csvText, setCsvText] = useState("");
  const [filename, setFilename] = useState<string | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [result, setResult] = useState<ImportCommitResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloadBusy, setDownloadBusy] = useState<string | null>(null);

  const contentB64 = csvText.trim().length > 0 ? encodeBase64Utf8(csvText) : null;

  const resetOutputs = () => {
    setPreview(null);
    setResult(null);
    setError(null);
  };

  const previewMutation = useMutation({
    mutationFn: () => previewCsvImport(filename, contentB64 ?? ""),
    onSuccess: (data) => {
      setPreview(data);
      setResult(null);
      setError(null);
    },
    onError: (err: Error) => {
      setPreview(null);
      setError(err.message);
    },
  });

  const commitMutation = useMutation({
    mutationFn: () => commitCsvImport(contentB64 ?? "", preview?.previewToken ?? ""),
    onSuccess: (data) => {
      setResult(data);
      setError(null);
    },
    onError: (err: Error) => setError(err.message),
  });

  const onPickFile = async (file: File | null) => {
    if (file === null) return;
    try {
      setCsvText(await readFileAsText(file));
      setFilename(file.name);
      resetOutputs();
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not read file");
    }
  };

  const onDownload = async (path: string, name: string) => {
    setDownloadBusy(path);
    setError(null);
    try {
      await downloadExportFile(path, name);
    } catch (err) {
      setError(err instanceof Error ? err.message : "download failed");
    } finally {
      setDownloadBusy(null);
    }
  };

  const commitEnabled =
    preview !== null && preview.recordCount > 0 && !commitMutation.isPending;

  return (
    <section className="card" id="data" aria-label="Data import and export">
      <header className="card-header">
        <h2>Data</h2>
      </header>

      {!ready && (
        <div className="skeleton" role="status" aria-label="Loading data tools">
          <div className="skeleton-bar" style={{ width: "44%" }} />
          <div className="skeleton-bar" style={{ width: "30%" }} />
        </div>
      )}

      {ready && token === null && (
        <div className="state">
          <p className="state-message">
            Sign in to import CSV files and export your data — imports and
            exports are owner operations.
          </p>
          <Link href="/login/" className="btn">
            Sign in
          </Link>
        </div>
      )}

      {ready && token !== null && (
        <>
          <p className="card-meta">
            Import a daily-metrics CSV (a <code>date</code> column plus metric
            columns), review the preview, then commit. Re-importing the same
            file is safe — duplicates only, never double rows.
          </p>

          {error !== null && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}

          <div className="field">
            <label htmlFor="csv-file">CSV file</label>
            <input
              id="csv-file"
              type="file"
              accept=".csv,text/csv"
              onChange={(event) => void onPickFile(event.target.files?.[0] ?? null)}
            />
            <p className="field-hint">
              …or paste the contents below. Comma or semicolon fields, dot or
              comma decimals, ISO or dd.mm.yyyy dates.
            </p>
          </div>

          <div className="field">
            <label htmlFor="csv-text">CSV contents</label>
            <textarea
              id="csv-text"
              rows={5}
              spellCheck={false}
              value={csvText}
              placeholder={"date,weight_kg,steps\n2026-01-05,80.0,9000"}
              onChange={(event) => {
                setCsvText(event.target.value);
                resetOutputs();
              }}
            />
          </div>

          <div className="confirm-group">
            <button
              type="button"
              className="btn btn-small"
              onClick={() => previewMutation.mutate()}
              disabled={contentB64 === null || previewMutation.isPending}
            >
              {previewMutation.isPending ? "Previewing…" : "Preview import"}
            </button>
            {preview !== null && !commitMutation.isPending && (
              <button
                type="button"
                className="btn btn-small"
                onClick={() => commitMutation.mutate()}
                disabled={!commitEnabled}
                title={
                  preview.recordCount === 0
                    ? "Nothing importable — see the warnings above"
                    : undefined
                }
              >
                Commit import
              </button>
            )}
            {commitMutation.isPending && <span className="confirm-label">Committing…</span>}
          </div>

          {preview !== null && (
            <div aria-live="polite">
              <PreviewSummary preview={preview} />
            </div>
          )}

          {result !== null && (
            <p className="caveats" aria-live="polite">
              Imported <span className="num">{result.recordsInserted}</span> of{" "}
              <span className="num">{result.recordsReceived}</span> records ·{" "}
              <span className="num">{result.recordsDuplicate}</span> duplicate
              {result.recordsDuplicate === 1 ? "" : "s"}
              {result.warnings.length > 0 && (
                <>
                  {" "}
                  ({result.warnings.join("; ")})
                </>
              )}
            </p>
          )}

          <div aria-label="Export">
            <p className="today-subhead">Export</p>
            <ul className="missing-list">
              {EXPORTS.map((entry) => (
                <li key={entry.path}>
                  <button
                    type="button"
                    className="btn btn-small"
                    onClick={() => void onDownload(entry.path, entry.filename)}
                    disabled={downloadBusy !== null}
                  >
                    {downloadBusy === entry.path ? "Preparing…" : entry.label}
                  </button>{" "}
                  — {entry.hint}
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </section>
  );
}
