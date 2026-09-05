"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import SiteHeader from "../components/site-header";
import { ApiError, loginAccount, registerAccount } from "../lib/api";
import { setStoredToken, useAccount } from "../lib/auth";

/** Mirrors USERNAME_RE in packages/contracts (pairing.py). */
const USERNAME_PATTERN = /^[a-z0-9](?:[a-z0-9._-]{1,62}[a-z0-9])?$/;

const PASSPHRASE_MIN = 12;

/**
 * Sign-in / first-run claim. GET /api/v1/auth/status decides the branch:
 * zero accounts on the server → claim form (POST /auth/register), otherwise
 * the login form (POST /auth/login). API errors surface inline with their
 * machine-readable error_code.
 */
export default function LoginPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const account = useAccount();

  const [username, setUsername] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [confirm, setConfirm] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [apiError, setApiError] = useState<ApiError | null>(null);
  const [networkError, setNetworkError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const isClaim = account.data?.hasAccount === false;

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setFieldError(null);
    setApiError(null);
    setNetworkError(null);

    if (!USERNAME_PATTERN.test(username)) {
      setFieldError(
        "Username must be 3-64 characters: lowercase letters, digits, and . _ - — not starting or ending with punctuation.",
      );
      return;
    }
    if (passphrase.length < PASSPHRASE_MIN) {
      setFieldError(`Passphrase must be at least ${PASSPHRASE_MIN} characters.`);
      return;
    }
    if (isClaim && passphrase !== confirm) {
      setFieldError("Passphrases do not match.");
      return;
    }

    setPending(true);
    try {
      const session = isClaim
        ? await registerAccount(username, passphrase)
        : await loginAccount(username, passphrase);
      setStoredToken(session.accessToken);
      router.push("/");
    } catch (error) {
      if (error instanceof ApiError) {
        setApiError(error);
        // Someone claimed the account in another tab: re-branch to login.
        if (error.errorCode === "ACCOUNT_EXISTS") {
          void queryClient.invalidateQueries({ queryKey: ["auth", "status"] });
        }
      } else {
        setNetworkError(
          "Could not reach the server. Check that the Somatriq API is running.",
        );
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="shell shell-narrow">
      <SiteHeader />

      <h1>{isClaim ? "Claim this server" : "Sign in"}</h1>

      {account.isPending && <p className="hint">Checking server state…</p>}

      {account.isError && (
        <div className="state" role="alert">
          <p className="state-message">
            Could not read the account status —{" "}
            {account.error instanceof Error ? account.error.message : "request failed"}.
          </p>
          <button
            type="button"
            className="btn"
            onClick={() => void account.refetch()}
          >
            Retry
          </button>
        </div>
      )}

      {account.data !== undefined && (
        <section aria-label={isClaim ? "Create the local account" : "Sign in"}>
          {isClaim ? (
            <p className="hint">
              First run — no account exists yet. This creates the single local
              account for this Somatriq server; registration closes once it exists.
            </p>
          ) : (
            <p className="hint">Sign in to pair devices and manage their access.</p>
          )}

          <form className="form" onSubmit={(event) => void handleSubmit(event)} noValidate>
            <div className="field">
              <label htmlFor="username">Username</label>
              <input
                id="username"
                name="username"
                type="text"
                autoComplete="username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                value={username}
                onChange={(event) => setUsername(event.target.value)}
              />
            </div>

            <div className="field">
              <label htmlFor="passphrase">Passphrase</label>
              <input
                id="passphrase"
                name="passphrase"
                type="password"
                autoComplete={isClaim ? "new-password" : "current-password"}
                minLength={PASSPHRASE_MIN}
                value={passphrase}
                onChange={(event) => setPassphrase(event.target.value)}
              />
              {isClaim && (
                <p className="field-hint">At least {PASSPHRASE_MIN} characters.</p>
              )}
            </div>

            {isClaim && (
              <div className="field">
                <label htmlFor="confirm">Confirm passphrase</label>
                <input
                  id="confirm"
                  name="confirm"
                  type="password"
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(event) => setConfirm(event.target.value)}
                />
              </div>
            )}

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
            {networkError !== null && (
              <p className="form-error" role="alert">
                {networkError}
              </p>
            )}

            <div className="form-actions">
              <button type="submit" className="btn btn-primary" disabled={pending}>
                {pending ? "Working…" : isClaim ? "Create account" : "Sign in"}
              </button>
            </div>
          </form>
        </section>
      )}
    </main>
  );
}
