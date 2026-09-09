import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Providers from "./providers";
import "./globals.css";

const sans = Geist({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const mono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "Somatriq",
    template: "%s · Somatriq",
  },
  description: "Private, self-hosted biometric intelligence built around a NOOP-based mobile collector.",
  applicationName: "Somatriq",
  manifest: "/manifest.webmanifest",
  icons: {
    icon: "/icon.svg",
  },
};

/**
 * Theme pre-hydration (design pass v2): apply the saved palette BEFORE first
 * paint so a dark/rose/blue reload never flashes teal. Static export keeps
 * this inline — no strategy budget to spend. Default (no storage / invalid
 * value) is the teal root.
 */
const THEME_BOOT_SCRIPT = `(function(){try{var t=localStorage.getItem("somatriq-theme");if(t&&["teal","ink","blue","green","rose"].indexOf(t)>=0){document.documentElement.dataset.theme=t;}}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body className={`${sans.variable} ${mono.variable}`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
