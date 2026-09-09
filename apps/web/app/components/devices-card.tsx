"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, changePassphrase, fetchDevices, revokeDevice } from "../lib/api";
import type { DeviceInfo } from "../lib/api";
import { useSession } from "../lib/auth";

const PASSPHRASE_MIN = 12;

function formatRelative(iso: string, now: number): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "unknown";
  const seconds = Math.round((now - then) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} d ago`;
  return new Date(then).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatDay(iso: string): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "unknown";
  return new Date(then).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function DeviceRow({ device, now }: { device: DeviceInfo; now: number }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const revoke = useMutation({
    mutationFn: () => revokeDevice(device.deviceId),
    onSuccess: () => {
      setConfirming(false);
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["devices"] });
    },
    onError: (err: Error) => setError(err.message),
  });

  const revoked = device.revokedAt !== null;

  return (
    <li className="device-row">
      <div className="device-main">
        <p className="device-name">
          {device.name}
          {revoked && (
            <span className="badge badge-revoked">revoked</span>
          )}
        </p>
        <p className="device-meta">
          {device.model !== null && (
            <>
              {device.model}
              <span aria-hidden="true"> · </span>
            </>
          )}
          paired {formatDay(device.createdAt)}
          <span aria-hidden="true"> · </span>
          last used{" "}
          {device.lastUsedAt === null ? "never" : formatRelative(device.lastUsedAt, now)}
        </p>
        {error !== null && (
          <p className="form-error" role="alert">
            Revoke failed — {error}
          </p>
        )}
      </div>
      {!revoked &&
        (confirming ? (
          <div className="confirm-group">
            <span className="confirm-label">Confirm revoke?</span>
            <button
              type="button"
              className="btn btn-danger btn-small"
              onClick={() => revoke.mutate()}
              disabled={revoke.isPending}
            >
              {revoke.isPending ? "Revoking…" : "Yes, revoke"}
            </button>
            <button
              type="button"
              className="btn btn-small"
              onClick={() => setConfirming(false)}
              disabled={revoke.isPending}
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            type="button"
            className="btn btn-small"
            onClick={() => setConfirming(true)}
          >
            Revoke
          </button>
        ))}
    </li>
  );
}

/**
 * Account passphrase rotation (POST /api/v1/auth/change-password). Lives in
 * the devices card — the account surface — but touches no device state:
 * paired devices and their tokens are independent credentials and keep
 * working (ADR 0015), which the success message states plainly. A 401 with
 * INVALID_CREDENTIALS is a wrong current passphrase (shown inline); only a
 * session-level 401 signs out.
 */
function ChangePassphrase() {
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [apiError, setApiError] = useState<ApiError | null>(null);
  const [saved, setSaved] = useState(false);

  const change = useMutation({
    mutationFn: () => changePassphrase(current, next),
    onSuccess: () => {
      setCurrent("");
      setNext("");
      setConfirm("");
      setFieldError(null);
      setApiError(null);
      setSaved(true);
    },
    onError: (err: Error) => {
      setSaved(false);
      if (err instanceof ApiError) {
        setApiError(err);
      } else {
        setFieldError("Could not reach the server. Check that the Somatriq API is running.");
      }
    },
  });

  const close = () => {
    setOpen(false);
    setCurrent("");
    setNext("");
    setConfirm("");
    setFieldError(null);
    setApiError(null);
    setSaved(false);
  };

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaved(false);
    setFieldError(null);
    setApiError(null);
    if (current.length === 0) {
      setFieldError("Enter your current passphrase.");
      return;
    }
    if (next.length < PASSPHRASE_MIN) {
      setFieldError(`New passphrase must be at least ${PASSPHRASE_MIN} characters.`);
      return;
    }
    if (next !== confirm) {
      setFieldError("New passphrases do not match.");
      return;
    }
    change.mutate();
  };

  if (!open) {
    return (
      <button
        type="button"
        className="btn btn-small account-toggle"
        onClick={() => setOpen(true)}
      >
        Change passphrase
      </button>
    );
  }

  return (
    <form className="card-form" onSubmit={submit} noValidate>
      <div className="field">
        <label htmlFor="pass-current">Current passphrase</label>
        <input
          id="pass-current"
          name="current"
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(event) => setCurrent(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="pass-new">New passphrase</label>
        <input
          id="pass-new"
          name="new"
          type="password"
          autoComplete="new-password"
          minLength={PASSPHRASE_MIN}
          value={next}
          onChange={(event) => setNext(event.target.value)}
        />
        <p className="field-hint">At least {PASSPHRASE_MIN} characters.</p>
      </div>
      <div className="field">
        <label htmlFor="pass-confirm">Confirm new passphrase</label>
        <input
          id="pass-confirm"
          name="confirm"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
        />
      </div>
      {fieldError !== null && (
        <p className="form-error" role="alert">
          {fieldError}
        </p>
      )}
      {apiError !== null && (
        <p className="form-error" role="alert">
          {apiError.errorCode !== null && (
            <>
              <span className="error-code">{apiError.errorCode}</span> —{" "}
            </>
          )}
          {apiError.message}
        </p>
      )}
      {saved && (
        <p className="field-hint" role="status">
          Passphrase changed. Your session and paired devices keep working.
        </p>
      )}
      <div className="form-actions">
        <button type="submit" className="btn btn-small btn-primary" disabled={change.isPending}>
          {change.isPending ? "Saving…" : "Save new passphrase"}
        </button>
        <button type="button" className="btn btn-small" onClick={close} disabled={change.isPending}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/**
 * Device roster (GET /api/v1/devices, account JWT). Handles its own auth
 * states: sign-in prompt when no token, quiet skeleton while the session
 * settles, empty state pointing at /pairing. The data cards follow the same
 * pattern — every read answers the owner's session (spec §122).
 */
export default function DevicesCard() {
  const { ready, token } = useSession();

  const query = useQuery({
    queryKey: ["devices"],
    queryFn: fetchDevices,
    enabled: ready && token !== null,
    staleTime: 30_000,
  });

  const devices = query.data ?? [];
  const now = Date.now();

  return (
    <section className="card" id="devices" aria-label="Paired devices">
      <header className="card-header">
        <h3>Devices</h3>
      </header>

      {!ready && <div className="skeleton" role="status" aria-label="Loading devices">
        <div className="skeleton-bar" style={{ width: "44%" }} />
        <div className="skeleton-bar" style={{ width: "30%" }} />
      </div>}

      {ready && token === null && (
        <div className="state">
          <p className="state-message">
            Sign in to see and manage the devices paired with this server.
          </p>
          <Link href="/login/" className="btn">
            Sign in
          </Link>
        </div>
      )}

      {ready && token !== null && query.isError && (
        <div className="state" role="alert">
          <p className="state-message">
            Could not load devices —{" "}
            {query.error instanceof Error ? query.error.message : "request failed"}.
          </p>
          <button type="button" className="btn" onClick={() => void query.refetch()}>
            Retry
          </button>
        </div>
      )}

      {ready && token !== null && query.isPending && (
        <div className="skeleton" role="status" aria-label="Loading devices">
          <div className="skeleton-bar" style={{ width: "44%" }} />
          <div className="skeleton-bar" style={{ width: "30%" }} />
        </div>
      )}

      {query.data !== undefined && devices.length === 0 && (
        <div className="state">
          <p className="state-message">
            No devices paired yet — pair your phone to start streaming
            biometric data.
          </p>
          <Link href="/pairing/" className="btn">
            Pair a device
          </Link>
        </div>
      )}

      {query.data !== undefined && devices.length > 0 && (
        <ul className="device-list">
          {devices.map((device) => (
            <DeviceRow key={device.deviceId} device={device} now={now} />
          ))}
        </ul>
      )}

      {ready && token !== null && <ChangePassphrase />}
    </section>
  );
}
