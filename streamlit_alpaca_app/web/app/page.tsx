import { redirect } from "next/navigation";
import { HomeWorkspace } from "@/components/home-workspace";
import { loadHomePublication } from "@/lib/server/home";

export const dynamic = "force-dynamic";

function normalizedDate(value: string | string[] | undefined) {
  const candidate = typeof value === "string" ? value.trim() : "";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(candidate)) return "";
  const parsed = new Date(`${candidate}T00:00:00Z`);
  const today = new Date();
  if (Number.isNaN(parsed.getTime()) || parsed.getTime() > today.getTime()) return "";
  return candidate;
}

export default async function HomePage({ searchParams }: { searchParams: Promise<{ date?: string | string[]; invite_token?: string | string[]; reset_token?: string | string[] }> }) {
  const params = await searchParams;
  // Preserve links already issued by the existing account service.
  const accountParams = new URLSearchParams();
  for (const key of ["invite_token", "reset_token"] as const) {
    const value = params[key];
    if (value !== undefined) accountParams.set(key, typeof value === "string" && value.length <= 256 ? value : "invalid");
  }
  if (accountParams.size) redirect(`/login?${accountParams.toString()}`);
  const targetDate = normalizedDate(params.date);
  const publication = await loadHomePublication(targetDate);
  return <HomeWorkspace key={targetDate || "latest"} initialHome={publication.home} initialEtag={publication.etag} targetDate={targetDate} />;
}
