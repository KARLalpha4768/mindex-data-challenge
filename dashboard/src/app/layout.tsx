import type { Metadata, Viewport } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Data Quality Review · Mindex Code Challenge",
  description:
    "Static review dashboard: 17 seeded data-quality defects, the decision taken on each, and a link to the exact line of pipeline code that handles it.",
  openGraph: {
    title: "Data Quality Review · Mindex Code Challenge",
    description:
      "Verified Python ETL & SQLite Star Schema data warehouse: 17/17 defect classes reconciled with $0.00 revenue drift.",
    type: "website",
    siteName: "Mindex Code Challenge Review Dashboard",
  },
  twitter: {
    card: "summary_large_image",
    title: "Data Quality Review · Mindex Code Challenge",
    description:
      "Verified Python ETL & SQLite Star Schema data warehouse: 17/17 defect classes reconciled with $0.00 revenue drift.",
  },
};

export const viewport: Viewport = {
  themeColor: "#08090b",
  width: "device-width",
  initialScale: 1,
};

/**
 * Root layout.
 *
 * Nothing but the document shell and a skip link. All state lives in the client
 * Dashboard component; this stays a Server Component so the page is fully
 * pre-rendered at build time.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {/* Skip link: first tab stop, visually hidden until focused. */}
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:border focus:border-accent focus:bg-panel focus:px-3 focus:py-2 focus:text-sm focus:text-ink"
        >
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
