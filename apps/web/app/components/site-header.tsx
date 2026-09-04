"use client";

import Link from "next/link";
import { signOut, useSession } from "../lib/auth";

/**
 * Minimal quiet header row shared by home, pairing and login: wordmark,
 * Devices/Pairing links, sign in/out. The session link flips after hydration
 * (static export — localStorage is invisible during prerender).
 */
export default function SiteHeader() {
  const { ready, token } = useSession();
  const signedIn = ready && token !== null;

  return (
    <header className="site-header">
      <Link href="/" className="wordmark">
        Somatriq
      </Link>
      <nav className="site-nav" aria-label="Primary">
        <Link href="/#devices">Devices</Link>
        <Link href="/pairing/">Pairing</Link>
        {signedIn ? (
          <button type="button" className="btn btn-small" onClick={signOut}>
            Sign out
          </button>
        ) : (
          <Link href="/login/">Sign in</Link>
        )}
      </nav>
    </header>
  );
}
