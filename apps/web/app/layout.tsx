import type { Metadata, Viewport } from "next";
// Self-hosted from npm, so the Docker build needs no font download.
import "@fontsource/fira-sans/400.css";
import "@fontsource/fira-sans/500.css";
import "@fontsource/fira-sans/600.css";
import "@fontsource/fira-code/500.css";
import "./globals.css";

export const metadata: Metadata = { title: "Trade Gateway", description: "Trusted Trade Agents: buyer workspace" };
export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#1e3a8a" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
