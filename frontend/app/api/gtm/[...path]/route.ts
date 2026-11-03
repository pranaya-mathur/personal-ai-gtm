import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const API = process.env.GTM_API_URL ?? "http://localhost:8000";

// Server-side proxy: the API token never reaches the browser. Access to this route is already
// gated by proxy.ts (dashboard login). Only read endpoints and the operator actions the
// dashboard uses are forwarded; approvals stay on the admin API.
const ALLOWED_POST = [/^runs$/, /^runs\/[0-9a-f-]+\/cancel$/];

async function forward(req: NextRequest, path: string[]): Promise<NextResponse> {
  const token = process.env.GTM_API_TOKEN;
  if (!token) return NextResponse.json({ detail: "GTM_API_TOKEN is not configured" }, { status: 503 });
  const target = path.join("/");
  if (req.method === "POST" && !ALLOWED_POST.some((r) => r.test(target))) {
    return NextResponse.json({ detail: "action not available from the dashboard" }, { status: 403 });
  }
  if (!["GET", "POST"].includes(req.method)) {
    return NextResponse.json({ detail: "method not allowed" }, { status: 405 });
  }
  const res = await fetch(`${API}/${target}${req.nextUrl.search}`, {
    method: req.method,
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: req.method === "POST" ? await req.text() : undefined,
    cache: "no-store",
  });
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "content-type": res.headers.get("content-type") ?? "application/json" },
  });
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await ctx.params).path);
}

export async function POST(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await ctx.params).path);
}
