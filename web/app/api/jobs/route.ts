import { NextResponse } from "next/server";
import { pool, recoverOrphans } from "@/lib/db";
import { JOB_LIST_LIMIT, MAX_ACTIVE_JOBS, TOPIC_MAX, TOPIC_MIN } from "@/lib/config";
import { runJob } from "@/lib/worker";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const COLS = "id, topic, status, progress, created_at, updated_at, (output_path IS NOT NULL) AS has_video";
const LOCK_KEY = 4242;

export async function GET() {
  try {
    await recoverOrphans();
    const { rows } = await pool.query(`SELECT ${COLS} FROM jobs ORDER BY id DESC LIMIT $1`, [JOB_LIST_LIMIT]);
    return NextResponse.json(rows);
  } catch (e) {
    console.error("[api] GET /api/jobs failed:", e);
    return NextResponse.json({ error: "Could not load jobs." }, { status: 500 });
  }
}

export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Request body must be JSON." }, { status: 400 });
  }
  const raw = (body as { topic?: unknown } | null)?.topic;
  if (typeof raw !== "string") {
    return NextResponse.json({ error: "Topic must be a string." }, { status: 400 });
  }
  const topic = raw.trim();
  if (topic.length < TOPIC_MIN || topic.length > TOPIC_MAX) {
    return NextResponse.json({ error: `Topic must be ${TOPIC_MIN}-${TOPIC_MAX} characters.` }, { status: 400 });
  }

  await recoverOrphans();
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    await client.query("SELECT pg_advisory_xact_lock($1)", [LOCK_KEY]); // serialize check+insert
    const { rows: c } = await client.query(
      "SELECT count(*)::int AS n FROM jobs WHERE status IN ('queued','running')",
    );
    if (c[0].n >= MAX_ACTIVE_JOBS) {
      await client.query("ROLLBACK");
      return NextResponse.json({ error: "Too many videos in progress. Please wait for one to finish." }, { status: 429 });
    }
    const { rows } = await client.query(`INSERT INTO jobs (topic) VALUES ($1) RETURNING ${COLS}`, [topic]);
    await client.query("COMMIT");
    runJob(rows[0].id, topic);
    return NextResponse.json(rows[0], { status: 201 });
  } catch (e) {
    await client.query("ROLLBACK").catch(() => {});
    console.error("[api] POST /api/jobs failed:", e);
    return NextResponse.json({ error: "Could not create the job." }, { status: 500 });
  } finally {
    client.release();
  }
}
