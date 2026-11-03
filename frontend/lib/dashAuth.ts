// Dashboard user-level authorization. Two separate Basic-auth identities:
//   operator: DASHBOARD_USER (default "owner") + DASHBOARD_PASSWORD
//   admin:    DASHBOARD_ADMIN_USER + DASHBOARD_ADMIN_PASSWORD (no default; must differ from operator)
// The role is derived from the request's own credentials on every call. Possession of the operator
// login never grants admin; the server-side API tokens are only what the proxy uses AFTER this check.
export type Role = "admin" | "operator";

function safeEqual(a: string, b: string): boolean {
  let diff = a.length ^ b.length;
  const n = Math.max(a.length, b.length);
  for (let i = 0; i < n; i++) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return diff === 0;
}

export function credentials(header: string | null): { user: string; password: string } | null {
  if (!header || !header.startsWith("Basic ")) return null;
  try {
    const [user, ...rest] = atob(header.slice(6)).split(":");
    return { user, password: rest.join(":") };
  } catch { return null; }
}

export function dashboardRole(header: string | null, env: Record<string, string | undefined>): Role | null {
  const c = credentials(header);
  if (!c) return null;
  const adminUser = env.DASHBOARD_ADMIN_USER, adminPw = env.DASHBOARD_ADMIN_PASSWORD;
  const opUser = env.DASHBOARD_USER ?? "owner", opPw = env.DASHBOARD_PASSWORD;
  const adminConfigured = !!adminUser && !!adminPw && adminPw !== opPw;
  if (adminConfigured && safeEqual(c.user, adminUser!) && safeEqual(c.password, adminPw!)) return "admin";
  if (opPw && safeEqual(c.user, opUser) && safeEqual(c.password, opPw)) return "operator";
  return null;
}
