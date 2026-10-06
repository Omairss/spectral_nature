import { LoginForm } from "./login-form";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string | string[] }>;
}) {
  const params = await searchParams;
  const raw = Array.isArray(params.next) ? params.next[0] : params.next;
  const destination = raw?.startsWith("/") && !raw.startsWith("//") ? raw : "/zopedia";
  return <LoginForm destination={destination} />;
}
