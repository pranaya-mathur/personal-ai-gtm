import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const API = process.env.GTM_API_URL ?? "http://localhost:8000";

// Server-side proxy: the API token never reaches the browser. Access to this route is already
// gated by proxy.ts (dashboard login). Approvals (admin) stay off this surface unless listed.
const ALLOWED_POST = [
  /^runs$/,
  /^runs\/[0-9a-f-]+\/cancel$/,
  /^intake$/,
  /^deals$/,
  /^deals\/[0-9a-f-]+\/interactions$/,
  /^drafts\/[0-9a-f-]+\/mark-sent$/,
];
const ALLOWED_PATCH = [/^deals\/[0-9a-f-]+$/];

async function forward(req: NextRequest, path: string[]): Promise<NextResponse> {
  const token = process.env.GTM_API_TOKEN;
  if (!token) return NextResponse.json({ detail: "GTM_API_TOKEN is not configured" }, { status: 503 });
  const target = path.join("/");
  if (req.method === "POST" && !ALLOWED_POST.some((r) => r.test(target))) {
    return NextResponse.json({ detail: "action not available from the dashboard" }, { status: 403 });
  }
  if (req.method === "PATCH" && !ALLOWED_PATCH.some((r) => r.test(target))) {
    return NextResponse.json({ detail: "action not available from the dashboard" }, { status: 403 });
  }
  if (!["GET", "POST", "PATCH"].includes(req.method)) {
    return NextResponse.json({ detail: "method not allowed" }, { status: 405 });
  }
  const res = await fetch(`${API}/${target}${req.nextUrl.search}`, {
    method: req.method,
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: ["POST", "PATCH"].includes(req.method) ? await req.text() : undefined,
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

export async function PATCH(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await ctx.params).path);
}
