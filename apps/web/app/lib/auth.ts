"use client";

/**
 * Session plumbing for the M2 account surface (ADR 0015). Token storage lives
 * in lib/api (the transport needs it for authFetch); this module re-exports it
 * for consumers and adds the React hooks.
 */

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { clearStoredToken, fetchAuthStatus, getStoredToken, setStoredToken } from "./api";

export { clearStoredToken, getStoredToken, setStoredToken };

/** Server-side account state — decides claim vs login on /login. */
export function useAccount() {
  return useQuery({
    queryKey: ["auth", "status"],
    queryFn: fetchAuthStatus,
    staleTime: 60_000,
  });
}

/**
 * Session token, read after hydration. With static export the prerendered
 * HTML cannot know about localStorage, so the first client render must match
 * it (token null) and only the effect may flip the state.
 */
export function useSession(): { ready: boolean; token: string | null } {
  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setToken(getStoredToken());
    setReady(true);
  }, []);

  return { ready, token };
}

/** Sign out: drop the credential and hard-navigate so all query state resets. */
export function signOut(): void {
  clearStoredToken();
  window.location.assign("/");
}
