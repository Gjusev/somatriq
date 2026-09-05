"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import { useMutation, useQuery } from "@tanstack/react-query";
import SiteHeader from "../components/site-header";
import { ApiError, createPairingSession, fetchPairingStatus } from "../lib/api";
import type { PairingSessionResponse } from "../lib/api";
import { useSession } from "../lib/auth";

const POLL_INTERVAL_MS = 2500;

function formatCountdown(remainingMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(remainingMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    return error.errorCode !== null ? `${error.errorCode} — ${error.message}` : error.message;
  }
  return error instanceof Error ? error.message : fallback;
}

/**
 * Device pairing wizard (web side of ADR 0015): create a session, show the
 * one-time 8-char code for typing into the phone, poll status until consumed
 * or expired. The full code exists only in the creation response — polling
 * returns a 4-char hint, and only the code's hash is stored server-side.
 * A QR code is future work; typing the code is the M2 path.
 */
export default function PairingPage() {
  const { ready, token } = useSession();
  const [session, setSession] = useState<PairingSessionResponse | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const create = useMutation({
    mutationFn: createPairingSession,
    onSuccess: (created) => {
      setNow(Date.now());
      setSession(created);
    },
  });

  const sessionId = session?.sessionId ?? null;

  const poll = useQuery({
    queryKey: ["pairing", sessionId],
    queryFn: () => {
      if (sessionId === null) {
        throw new Error("no active pairing session");
      }
      return fetchPairingStatus(sessionId);
    },
    enabled: sessionId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "consumed" || status === "expired") return false;
      // Server no longer knows the session (restart / cleanup): stop polling.
      const error = query.state.error;
      if (
        error instanceof ApiError &&
        (error.status === 404 || error.status === 410)
      ) {
        return false;
      }
      return POLL_INTERVAL_MS;
    },
  });

  // Live countdown to expires_at; ticks only while a session is on screen.
  useEffect(() => {
    if (session === null) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(id);
  }, [session]);

  const pollError = poll.error;
  const pollGone =
    pollError instanceof ApiError && (pollError.status === 404 || pollError.status === 410);
  const status = poll.data?.status ?? "pending";
  const device = poll.data?.device ?? null;

  function startNewSession(): void {
    create.reset();
    setSession(null);
    create.mutate();
  }

  function renderSession(): ReactNode {
    if (session === null) return null;
    const remaining = Date.parse(session.expiresAt) - now;

    if (pollGone) {
      return (
        <div className="state" role="alert">
          <p className="state-message">
            The server no longer knows this pairing session{pollError instanceof ApiError ? ` — ${pollError.message}` : ""}.
          </p>
          <button type="button" className="btn" onClick={startNewSession}>
            Create a new session
          </button>
        </div>
      );
    }

    if (status === "consumed" && device !== null) {
      return (
        <div className="state">
          <p className="state-message">
            <strong>Paired.</strong> {device.name}
            {device.model !== null ? ` (${device.model})` : ""} received its device
            token — it was shown once on the phone and is stored there.
          </p>
          <Link href="/" className="btn">
            Back to overview
          </Link>
        </div>
      );
    }

    if (status === "consumed") {
      return (
        <div className="state">
          <p className="state-message">
            <strong>Paired.</strong> The device received its token.
          </p>
          <Link href="/" className="btn">
            Back to overview
          </Link>
        </div>
      );
    }

    if (status === "expired") {
      return (
        <div className="state">
          <p className="state-message">
            The pairing code expired — codes are valid for 10 minutes and single use.
          </p>
          <button type="button" className="btn" onClick={startNewSession}>
            Create a new session
          </button>
        </div>
      );
    }

    // pending (or the first poll still in flight)
    return (
      <div className="pairing-live">
        <p className="pairing-instruction">
          Type this code into Somatriq on your phone.
        </p>
        <p className="pairing-code" aria-label={`Pairing code ${session.pairingCode}`}>
          {session.pairingCode.slice(0, 4)}
          <span aria-hidden="true"> </span>
          {session.pairingCode.slice(4)}
        </p>
        <p className="pairing-meta">
          <span className="num">expires in {formatCountdown(remaining)}</span>
          <span aria-hidden="true"> · </span>
          <span className="awaiting">
            <span className="awaiting-dot" aria-hidden="true" />
            awaiting device
          </span>
        </p>
        {poll.data !== undefined && (
          <p className="hint">
            Status checks only see the first four characters ({poll.data.pairingCodeHint}····)
            — the full code is stored server-side as a hash. A scannable QR code
            is planned; for now, type the code.
          </p>
        )}
        {poll.isError && !pollGone && (
          <p className="form-error" role="alert">
            Status check failed — {errorMessage(pollError, "request failed")}. Retrying.
          </p>
        )}
      </div>
    );
  }

  return (
    <main className="shell shell-narrow">
      <SiteHeader />
      <h1>Pair a device</h1>

      {!ready && <p className="hint">Checking session…</p>}

      {ready && token === null && (
        <div className="state">
          <p className="state-message">
            Pairing requires signing in — the pairing session is created with
            your account.
          </p>
          <Link href="/login/" className="btn">
            Sign in
          </Link>
        </div>
      )}

      {ready && token !== null && session === null && (
        <section aria-label="Create pairing session">
          <p className="hint">
            Creates a one-time 8-character code. Enter it in the Somatriq app on
            your phone; the code is valid for 10 minutes and can be used once.
          </p>
          {create.isError && (
            <p className="form-error" role="alert">
              {errorMessage(create.error, "request failed")}
            </p>
          )}
          <div className="form-actions">
            <button
              type="button"
              className="btn"
              onClick={() => create.mutate()}
              disabled={create.isPending}
            >
              {create.isPending ? "Creating…" : "Create pairing session"}
            </button>
          </div>
        </section>
      )}

      {ready && token !== null && session !== null && renderSession()}
    </main>
  );
}
