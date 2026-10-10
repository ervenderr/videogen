import { Pool } from "pg";

const g = globalThis as unknown as { pgPool?: Pool };

export const pool: Pool =
  g.pgPool ?? (g.pgPool = new Pool({ connectionString: process.env.DATABASE_URL }));

// Child processes die with the server, so any queued/running row at startup is orphaned.
// Runs once per process, lazily from the API routes.
export function recoverOrphans(): Promise<void> {
  const h = globalThis as unknown as { orphanRecovery?: Promise<void> };
  return (h.orphanRecovery ??= pool
    .query(
      "UPDATE jobs SET status='failed', error='server restarted', updated_at=now() WHERE status IN ('queued','running')",
    )
    .then((r) => {
      if (r.rowCount) console.warn(`[startup] marked ${r.rowCount} orphaned job(s) as failed`);
    })
    .catch((e) => {
      h.orphanRecovery = undefined; // retry on next request
      console.error("[startup] orphan recovery failed:", e);
    }));
}
