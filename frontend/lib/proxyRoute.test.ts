import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET, POST, PATCH } from "../app/api/gtm/[...path]/route";
import { proxy } from "../proxy";

const ID = "11111111-1111-1111-1111-111111111111";
const ctx = (p: string) => ({ params: Promise.resolve({ path: p.split("/") }) });
const basic = (u: string, p: string) => "Basic " + btoa(`${u}:${p}`);
const OP = basic("owner", "op-pass"), ADM = basic("boss", "admin-pass");
const req = (method: string, path: string, headers: Record<string, string> = {}) =>
  new NextRequest(`http://dash.local/api/gtm/${path}`, { method, headers: { host: "dash.local", authorization: OP, ...headers }, body: method === "GET" ? undefined : "{}" });

let sent: { url: string; auth: string }[] = [];
beforeEach(() => {
  sent = [];
  process.env.GTM_API_TOKEN = "OPERATOR-TOKEN";
  process.env.GTM_ADMIN_API_TOKEN = "ADMIN-TOKEN";
  process.env.DASHBOARD_PASSWORD = "op-pass";
  process.env.DASHBOARD_ADMIN_USER = "boss";
  process.env.DASHBOARD_ADMIN_PASSWORD = "admin-pass";
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    sent.push({ url, auth: (init.headers as Record<string, string>).authorization });
    return new Response("{}", { status: 200 });
  }));
});
afterEach(() => { vi.unstubAllGlobals(); for (const k of ["GTM_ADMIN_API_TOKEN", "DASHBOARD_PASSWORD", "DASHBOARD_ADMIN_USER", "DASHBOARD_ADMIN_PASSWORD", "DASHBOARD_USER"]) delete process.env[k]; });

describe("dashboard proxy security", () => {
  it("approval uses the admin token; operator actions and reads never do", async () => {
    await POST(req("POST", `drafts/${ID}/approve`, { "x-gtm-action": "1", authorization: ADM }), ctx(`drafts/${ID}/approve`));
    await POST(req("POST", `opportunities/${ID}/verify`, { "x-gtm-action": "1" }), ctx(`opportunities/${ID}/verify`));
    await GET(req("GET", "schedules"), ctx("schedules"));
    expect(sent.map((s) => s.auth)).toEqual(["Bearer ADMIN-TOKEN", "Bearer OPERATOR-TOKEN", "Bearer OPERATOR-TOKEN"]);
  });
  it("refuses approvals when the admin token is not configured (fails closed, no fallback to operator)", async () => {
    delete process.env.GTM_ADMIN_API_TOKEN;
    const r = await POST(req("POST", `proposals/${ID}/approve`, { "x-gtm-action": "1", authorization: ADM }), ctx(`proposals/${ID}/approve`));
    expect(r.status).toBe(403);
    expect(sent).toHaveLength(0);
  });
  it("refuses cross-origin and header-less mutating requests before any upstream call", async () => {
    const evil = await POST(req("POST", `drafts/${ID}/approve`, { authorization: ADM, origin: "https://evil.example" }), ctx(`drafts/${ID}/approve`));
    const bare = await PATCH(req("PATCH", `schedules/ted_api`), ctx("schedules/ted_api"));
    expect([evil.status, bare.status]).toEqual([403, 403]);
    expect(sent).toHaveLength(0);
  });
  it("does not expose non-allow-listed or unsupported actions", async () => {
    const a = await POST(req("POST", "capabilities", { "x-gtm-action": "1" }), ctx("capabilities"));
    const b = await PATCH(req("PATCH", "drafts/../runs", { "x-gtm-action": "1" }), ctx("drafts/../runs"));
    expect([a.status, b.status]).toEqual([403, 403]);
    expect(sent).toHaveLength(0);
  });
  it("503s without an operator token and never forwards the browser's own authorization header", async () => {
    delete process.env.GTM_API_TOKEN;
    expect((await GET(req("GET", "board"), ctx("board"))).status).toBe(503);
    process.env.GTM_API_TOKEN = "OPERATOR-TOKEN";
    await GET(req("GET", "board"), ctx("board"));
    expect((await GET(req("GET", "board", { authorization: "Bearer attacker" }), ctx("board"))).status).toBe(401);
    expect(sent[0].auth).toBe("Bearer OPERATOR-TOKEN");
  });
  it("PRIVILEGE ESCALATION: operator-only login cannot reach any admin route, with any header tricks", async () => {
    const adminRoutes: [typeof POST, string][] = [
      [POST, `drafts/${ID}/approve`], [POST, `proposals/${ID}/approve`], [PATCH, "schedules/ted_api"],
    ];
    for (const [fn, path] of adminRoutes) {
      const m = fn === POST ? "POST" : "PATCH";
      const plain = await fn(req(m, path, { "x-gtm-action": "1" }), ctx(path));
      const spoof = await fn(req(m, path, { "x-gtm-action": "1", "x-gtm-role": "admin", "x-forwarded-user": "boss" }), ctx(path));
      const wrongAdmin = await fn(req(m, path, { "x-gtm-action": "1", authorization: basic("boss", "op-pass") }), ctx(path));
      expect([plain.status, spoof.status, wrongAdmin.status]).toEqual([403, 403, 401]);
    }
    expect(sent).toHaveLength(0); // the admin token was never used and nothing reached the API
  });
  it("whoami reports the role from the credentials; admin login is disabled if its password equals the operator's", async () => {
    const who = async (a: string) => (await (await GET(req("GET", "whoami", { authorization: a }), ctx("whoami"))).json()).role;
    expect([await who(OP), await who(ADM), await who(basic("owner", "x"))]).toEqual(["operator", "admin", null]);
    process.env.DASHBOARD_ADMIN_PASSWORD = "op-pass";
    expect(await who(basic("boss", "op-pass"))).toBeNull();
  });
  it("unauthenticated requests get 401 before anything is forwarded", async () => {
    const r = await GET(new NextRequest("http://dash.local/api/gtm/board", { headers: { host: "dash.local" } }), ctx("board"));
    expect(r.status).toBe(401);
    expect(sent).toHaveLength(0);
  });
  it("dashboard login fails closed without a password and rejects wrong credentials", () => {
    const mk = (auth?: string) => new NextRequest("http://dash.local/", { headers: auth ? { authorization: auth } : {} });
    delete process.env.DASHBOARD_PASSWORD;
    expect(proxy(mk()).status).toBe(503);
    process.env.DASHBOARD_PASSWORD = "pw";
    expect(proxy(mk()).status).toBe(401);
    expect(proxy(mk("Basic " + btoa("owner:wrong"))).status).toBe(401);
    expect(proxy(mk("Basic " + btoa("owner:pw"))).status).toBe(200);
  });
});
