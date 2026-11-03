import { NextRequest, NextResponse } from "next/server";
import { pickToken, sameOrigin } from "@/lib/proxyPolicy";

export const dynamic = "force-dynamic";

const API = process.env.GTM_API_URL ?? "http://localhost:8000";

// Server-side proxy: API tokens never reach the browser. Access to this route is already gated by
// proxy.ts (dashboard login); what may be called, and with which token, is lib/proxyPolicy.ts.
async function forward(req: NextRequest, path: string[]): Promise<NextResponse> {
  const target = path.join("/");
  if (["POST", "PATCH"].includes(req.method) && !sameOrigin(req)) {
    return NextResponse.json({ detail: "cross-origin request refused" }, { status: 403 });
  }
  const picked = pickToken(req.method, target, process.env);
  if (picked.forbidden) return NextResponse.json({ detail: picked.forbidden }, { status: 403 });
  const token = picked.token;
  if (!token) return NextResponse.json({ detail: "GTM_API_TOKEN is not configured" }, { status: 503 });
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
