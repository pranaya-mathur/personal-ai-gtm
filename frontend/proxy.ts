import { NextRequest, NextResponse } from "next/server";

// Dashboard login (HTTP Basic). Fails closed: with no DASHBOARD_PASSWORD nothing is served,
// unless DASHBOARD_AUTH_DISABLED=true is set explicitly for local development.
export function proxy(req: NextRequest) {
  if (process.env.DASHBOARD_AUTH_DISABLED === "true") return NextResponse.next();
  const user = process.env.DASHBOARD_USER ?? "owner";
  const password = process.env.DASHBOARD_PASSWORD;
  if (!password) {
    return new NextResponse("Dashboard login is not configured (set DASHBOARD_PASSWORD).", { status: 503 });
  }
  const header = req.headers.get("authorization") ?? "";
  if (header.startsWith("Basic ")) {
    const [u, ...rest] = atob(header.slice(6)).split(":");
    if (u === user && rest.join(":") === password) return NextResponse.next();
  }
  return new NextResponse("Authentication required", {
    status: 401,
    headers: { "www-authenticate": 'Basic realm="GTM Command Center"' },
  });
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
