import { spawn } from "node:child_process";
import path from "node:path";
import { pool } from "./db";
import { OUTPUT_DIR, PYTHON_BIN, SCRIPT_DIR, SCRIPT_PATH, STDERR_TAIL_CHARS } from "./config";

const PROGRESS_RE = /^\d\/\d\b.*/;

async function update(id: number, fields: { status?: string; progress?: string; output_path?: string; error?: string }) {
  await pool.query(
    `UPDATE jobs SET status = COALESCE($2, status), progress = COALESCE($3, progress),
       output_path = COALESCE($4, output_path), error = COALESCE($5, error), updated_at = now()
     WHERE id = $1`,
    [id, fields.status ?? null, fields.progress ?? null, fields.output_path ?? null, fields.error ?? null],
  );
}

function logErr(id: number, err: unknown) {
  console.error(`[worker] job ${id} db update failed:`, err);
}

export function runJob(id: number, topic: string) {
  const out = path.join(OUTPUT_DIR, `${id}.mp4`);
  // Argument array, no shell: the topic is a single argv entry and never parsed by a shell.
  const child = spawn(PYTHON_BIN, [SCRIPT_PATH, topic, out], {
    cwd: SCRIPT_DIR,
    shell: false,
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
    stdio: ["ignore", "pipe", "pipe"],
  });

  let stderrTail = "";
  let buf = "";
  let finished = false;

  update(id, { status: "running", progress: "starting" }).catch((e) => logErr(id, e));

  child.stdout.on("data", (chunk: Buffer) => {
    buf += chunk.toString();
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";
    for (const line of lines) {
      if (PROGRESS_RE.test(line)) update(id, { progress: line.trim() }).catch((e) => logErr(id, e));
    }
  });

  child.stderr.on("data", (chunk: Buffer) => {
    stderrTail = (stderrTail + chunk.toString()).slice(-STDERR_TAIL_CHARS);
  });

  const fail = (msg: string) => {
    if (finished) return;
    finished = true;
    console.error(`[worker] job ${id} failed: ${msg}`);
    update(id, { status: "failed", error: msg }).catch((e) => logErr(id, e));
  };

  child.on("error", (e) => fail(`spawn error: ${e.message}`));
  child.on("close", (code) => {
    if (code === 0) {
      if (finished) return;
      finished = true;
      update(id, { status: "done", progress: "done", output_path: out }).catch((e) => logErr(id, e));
    } else {
      fail(`exit code ${code}: ${stderrTail.trim() || "no stderr"}`);
    }
  });
}
