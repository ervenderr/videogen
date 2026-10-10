import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import path from "node:path";
import { Readable } from "node:stream";
import { NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { OUTPUT_DIR } from "@/lib/config";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const err = (status: number, error: string) => NextResponse.json({ error }, { status });

export async function GET(req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  if (!/^\d{1,9}$/.test(id)) return err(400, "Invalid id.");

  try {
    const { rows } = await pool.query("SELECT output_path FROM jobs WHERE id = $1 AND status = 'done'", [Number(id)]);
    const file = rows[0]?.output_path ? path.resolve(rows[0].output_path) : null;
    if (!file || path.dirname(file) !== path.resolve(OUTPUT_DIR)) return err(404, "Video not found.");

    const size = (await stat(file)).size;
    const range = req.headers.get("range");
    let start = 0;
    let end = size - 1;
    let status = 200;

    if (range) {
      const m = /^bytes=(\d*)-(\d*)$/.exec(range);
      if (!m || (m[1] === "" && m[2] === "")) return err(416, "Bad range.");
      if (m[1] === "") {
        start = Math.max(size - Number(m[2]), 0); // suffix range
      } else {
        start = Number(m[1]);
        if (m[2] !== "") end = Math.min(Number(m[2]), size - 1);
      }
      if (start > end || start >= size) {
        return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${size}` } });
      }
      status = 206;
    }

    const stream = Readable.toWeb(createReadStream(file, { start, end })) as ReadableStream;
    const headers: Record<string, string> = {
      "Content-Type": "video/mp4",
      "Accept-Ranges": "bytes",
      "Content-Length": String(end - start + 1),
    };
    if (status === 206) headers["Content-Range"] = `bytes ${start}-${end}/${size}`;
    return new Response(stream, { status, headers });
  } catch (e) {
    console.error("[api] video failed:", e);
    return err(500, "Could not load the video.");
  }
}
