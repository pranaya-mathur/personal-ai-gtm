import { NextRequest, NextResponse } from "next/server";
import { dashboardRole } from "@/lib/dashAuth";

// Dashboard login (HTTP Basic). Fails closed: with no DASHBOARD_PASSWORD nothing is served,
// unless DASHBOARD_AUTH_DISABLED=true is set explicitly for local development.
export function proxy(req: NextRequest) {
  if (process.env.DASHBOARD_AUTH_DISABLED === "true") return NextResponse.next(); // dev: operator at most
  if (!process.env.DASHBOARD_PASSWORD) {
    return new NextResponse("Dashboard login is not configured (set DASHBOARD_PASSWORD).", { status: 503 });
  }
  if (dashboardRole(req.headers.get("authorization"), process.env)) return NextResponse.next();
  return new NextResponse("Authentication required", {
    status: 401,
    headers: { "www-authenticate": 'Basic realm="GTM Command Center"' },
  });
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
