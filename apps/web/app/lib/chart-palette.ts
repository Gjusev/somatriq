"use client";

import { useEffect, useState } from "react";

/**
 * Shared chart palette (design pass 2026-09-05): every chart card reads its
 * colors from the SAME CSS tokens, so light/dark stay in sync and the accent
 * identity lives in exactly one place. Replaces the three per-card copies.
 */
export interface ChartPalette {
  ink: string;
  muted: string;
  paper: string;
  /** Accent (deep teal light / luminous teal dark) — series lines and bars. */
  accent: string;
}

const FALLBACK: ChartPalette = {
  ink: "#18181b",
  muted: "#71717a",
  paper: "#fafafa",
  accent: "#0f766e",
};

/** Bump a counter when the color scheme flips OR the user picks another theme
 * (design pass v2) so charts re-read the CSS tokens. */
export function useColorSchemeVersion(): number {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setVersion((n) => n + 1);
    query.addEventListener("change", onChange);
    const onTheme = () => setVersion((n) => n + 1);
    window.addEventListener("somatriq-theme", onTheme);
    return () => {
      query.removeEventListener("change", onChange);
      window.removeEventListener("somatriq-theme", onTheme);
    };
  }, []);
  return version;
}

/** Read palette values from the existing CSS tokens (prerender: fallbacks only). */
export function readChartPalette(): ChartPalette {
  if (typeof window === "undefined") {
    return FALLBACK;
  }
  const styles = getComputedStyle(document.documentElement);
  return {
    ink: styles.getPropertyValue("--ink").trim() || FALLBACK.ink,
    muted: styles.getPropertyValue("--muted").trim() || FALLBACK.muted,
    paper: styles.getPropertyValue("--paper").trim() || FALLBACK.paper,
    accent: styles.getPropertyValue("--accent").trim() || FALLBACK.accent,
  };
}
