import { NextResponse } from "next/server";
import { pool } from "@/lib/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  if (!/^\d{1,9}$/.test(id)) return NextResponse.json({ error: "Invalid id." }, { status: 400 });
  try {
    const { rows } = await pool.query(
      "SELECT id, topic, status, progress, created_at, updated_at, (output_path IS NOT NULL) AS has_video FROM jobs WHERE id = $1",
      [Number(id)],
    );
    if (!rows[0]) return NextResponse.json({ error: "Job not found." }, { status: 404 });
    return NextResponse.json(rows[0]);
  } catch (e) {
    console.error("[api] GET /api/jobs/[id] failed:", e);
    return NextResponse.json({ error: "Could not load the job." }, { status: 500 });
  }
}
