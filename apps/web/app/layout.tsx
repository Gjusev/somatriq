import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Somatriq",
  description: "Personal biometric intelligence. Your body. Your data. Your intelligence.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
