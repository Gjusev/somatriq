"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchDevices, revokeDevice } from "../lib/api";
import type { DeviceInfo } from "../lib/api";
import { useSession } from "../lib/auth";

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
        <h2>Devices</h2>
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
    </section>
  );
}
