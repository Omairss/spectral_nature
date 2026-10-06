import type { Metadata } from "next";
import { connection } from "next/server";
import { WorkbenchShell } from "@/components/workbench-shell";
import "plotly.js/dist/plotly.css";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Spectral Nature", template: "%s · Spectral Nature" },
  description: "Market intelligence and evidence-grounded research.",
  robots: { index: false, follow: false },
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  await connection();
  return (
    <html lang="en">
      <body>
        <WorkbenchShell>{children}</WorkbenchShell>
      </body>
    </html>
  );
}
