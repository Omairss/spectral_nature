import { issueCsrfToken } from "@/lib/server/auth";
import { noStoreJson } from "@/lib/server/http";

export async function GET() {
  const csrfToken = await issueCsrfToken();
  return noStoreJson({ csrfToken });
}
