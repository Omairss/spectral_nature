import { LoginForm } from "./login-form";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string | string[]; invite_token?: string | string[]; reset_token?: string | string[]; notice?: string }>;
}) {
  const params = await searchParams;
  const raw = Array.isArray(params.next) ? params.next[0] : params.next;
  const destination = raw?.startsWith("/") && !raw.startsWith("//") ? raw : "/zopedia";
  const hasInvite = params.invite_token !== undefined;
  const hasReset = params.reset_token !== undefined;
  const token = hasInvite ? params.invite_token : params.reset_token;
  const validToken = typeof token === "string" && /^[A-Za-z0-9_-]{32,256}$/.test(token);
  const invalidLink = (hasInvite || hasReset) && (hasInvite === hasReset || !validToken);
  const action = !invalidLink && hasInvite ? "invite" : !invalidLink && hasReset ? "reset" : "login";
  return <LoginForm action={action} destination={destination} token={validToken ? token : ""} invalidLink={invalidLink} notice={params.notice === "reset" ? "Password updated. Sign in." : ""} />;
}
