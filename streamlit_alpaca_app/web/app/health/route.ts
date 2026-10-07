import { noStoreJson } from "@/lib/server/http";

export function GET() {
  return noStoreJson({ status: "ok" });
}
