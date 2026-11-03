// Which dashboard actions may reach the API, and which server-side token carries them.
export const ALLOWED_POST = [
  /^runs$/,
  /^opportunities\/[0-9a-f-]+\/verify$/,
  /^drafts\/[0-9a-f-]+\/reject$/,
  /^runs\/[0-9a-f-]+\/cancel$/,
  /^intake$/,
  /^deals$/,
  /^deals\/[0-9a-f-]+\/interactions$/,
  /^drafts\/[0-9a-f-]+\/mark-sent$/,
  /^opportunities\/[0-9a-f-]+\/(draft|proposal)$/,
  /^deals\/[0-9a-f-]+\/onboarding$/,
  /^proposals\/[0-9a-f-]+\/mark-sent$/,
];
const ALLOWED_PATCH = [
  /^deals\/[0-9a-f-]+$/, /^onboarding\/[0-9a-f-]+$/, /^proposals\/[0-9a-f-]+$/, /^drafts\/[0-9a-f-]+$/,
];
// Admin-only actions. They use a SEPARATE server-side token (GTM_ADMIN_API_TOKEN) that never
// reaches the browser; the backend still enforces the admin role and records the approver.
const ADMIN_POST = [/^drafts\/[0-9a-f-]+\/approve$/, /^proposals\/[0-9a-f-]+\/approve$/];
const ADMIN_PATCH = [/^schedules\/[a-z0-9_]+$/];

/** CSRF guard for the Basic-auth dashboard: a mutating call must come from this origin. */
export function sameOrigin(req: { headers: { get(n: string): string | null } }): boolean {
  const origin = req.headers.get("origin");
  const host = req.headers.get("host");
  if (!origin) return req.headers.get("x-gtm-action") === "1"; // non-browser or fetch from our UI
  try { return new URL(origin).host === host; } catch { return false; }
}

export function pickToken(method: string, target: string, env: Record<string, string | undefined>, role: "admin" | "operator" | null = null):
  { token?: string; forbidden?: string } {
  const admin = (method === "POST" && ADMIN_POST.some((r) => r.test(target))) ||
    (method === "PATCH" && ADMIN_PATCH.some((r) => r.test(target)));
  if (admin) {
    // user-level check FIRST: the admin API token is only reachable by a signed-in admin user
    if (role !== "admin") return { forbidden: "admin sign-in required: your dashboard login is operator-level" };
    return env.GTM_ADMIN_API_TOKEN
      ? { token: env.GTM_ADMIN_API_TOKEN }
      : { forbidden: "admin approval is not configured for the dashboard (GTM_ADMIN_API_TOKEN)" };
  }
  if (method === "POST" && !ALLOWED_POST.some((r) => r.test(target))) return { forbidden: "action not available from the dashboard" };
  if (method === "PATCH" && !ALLOWED_PATCH.some((r) => r.test(target))) return { forbidden: "action not available from the dashboard" };
  return { token: env.GTM_API_TOKEN };
}
