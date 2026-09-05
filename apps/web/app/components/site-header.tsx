"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { signOut, useSession } from "../lib/auth";

/**
 * The user-selectable palettes (design pass v2). The swatch shows each
 * theme's LIGHT accent — a fixed preview so the row reads as a palette
 * independent of the active scheme. Values must match the [data-theme]
 * blocks in globals.css and the boot script's allowlist in layout.tsx.
 */
const THEMES: { id: string; label: string; swatch: string }[] = [
  { id: "teal", label: "Teal", swatch: "#0f766e" },
  { id: "ink", label: "Ink (monochrome)", swatch: "#18181b" },
  { id: "blue", label: "Steel blue", swatch: "#28598f" },
  { id: "green", label: "Forest green", swatch: "#237a46" },
  { id: "rose", label: "Deep rose", swatch: "#b03a5b" },
];

type Health = "checking" | "ok" | "bad";

function ThemePicker() {
  const [theme, setTheme] = useState("teal");

  useEffect(() => {
    const saved = document.documentElement.dataset.theme;
    if (saved) setTheme(saved);
  }, []);

  const pick = (id: string) => {
    setTheme(id);
    document.documentElement.dataset.theme = id;
    try {
      localStorage.setItem("somatriq-theme", id);
    } catch {
      // Private-mode storage failures just lose the preference across reloads.
    }
    // Charts re-read the CSS tokens on this event (lib/chart-palette).
    window.dispatchEvent(new Event("somatriq-theme"));
  };

  return (
    <div className="theme-picker" role="radiogroup" aria-label="Color theme">
      {THEMES.map((t) => (
        <button
          key={t.id}
          type="button"
          role="radio"
          aria-checked={theme === t.id}
          title={t.label}
          aria-label={t.label}
          className="theme-swatch"
          style={{ background: t.swatch }}
          onClick={() => pick(t.id)}
        />
      ))}
    </div>
  );
}

/**
 * Quiet header row shared by home, pairing and login: wordmark, theme
 * picker, API-health dot, Devices/Pairing links, sign in/out. The session
 * link flips after hydration (static export — localStorage is invisible
 * during prerender).
 */
export default function SiteHeader() {
  const { ready, token } = useSession();
  const signedIn = ready && token !== null;
  const [health, setHealth] = useState<Health>("checking");

  useEffect(() => {
    fetch("/api/health")
      .then((res) => setHealth(res.ok ? "ok" : "bad"))
      .catch(() => setHealth("bad"));
  }, []);

  return (
    <header className="site-header">
      <div className="site-header-left">
        <Link href="/" className="wordmark">
          Somatriq
        </Link>
        <span
          className={`status-dot ${health === "ok" ? "status-dot-ok" : health === "bad" ? "status-dot-bad" : ""}`}
          role="status"
          aria-label={`API ${health === "ok" ? "reachable" : health === "bad" ? "unreachable" : "checking"}`}
        />
      </div>
      <nav className="site-nav" aria-label="Primary">
        <ThemePicker />
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
