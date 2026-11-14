import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "Trade Gateway", description: "Trusted Trade Agents: buyer workspace" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
