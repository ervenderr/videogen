"use client";
import { useCallback, useEffect, useState } from "react";
import { POLL_MS, TOPIC_MAX } from "@/lib/config";

type Job = { id: number; topic: string; status: string; progress: string | null; has_video: boolean };

export default function Home() {
  const [topic, setTopic] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/jobs", { cache: "no-store" });
      if (!r.ok) throw new Error(String(r.status));
      setJobs(await r.json());
    } catch (e) {
      console.error("load jobs failed", e);
      setMsg("Could not refresh the job list. Retrying...");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg("");
    try {
      const r = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic }),
      });
      if (r.ok) {
        setTopic("");
        await load();
      } else {
        const body = await r.json().catch(() => null);
        setMsg(body?.error ?? "Something went wrong. Please try again.");
      }
    } catch (e) {
      console.error("create job failed", e);
      setMsg("Could not reach the server. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <h1>Videogen</h1>
      <form onSubmit={submit} style={{ display: "flex", gap: 8 }}>
        <input
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          maxLength={TOPIC_MAX}
          placeholder="Topic, e.g. Aurora borealis"
          style={{ flex: 1, padding: 8 }}
        />
        <button disabled={busy} style={{ padding: "8px 16px" }}>{busy ? "..." : "Generate"}</button>
      </form>
      {msg && <p role="alert" style={{ color: "#b00020" }}>{msg}</p>}
      <ul style={{ listStyle: "none", padding: 0 }}>
        {jobs.map((j) => (
          <li key={j.id} style={{ border: "1px solid #ccc", borderRadius: 6, padding: 12, margin: "12px 0" }}>
            <strong>{j.topic}</strong> <small>#{j.id}</small>
            <div>
              {j.status}
              {j.status === "running" && j.progress ? `: ${j.progress}` : ""}
            </div>
            {j.status === "failed" && (
              <div style={{ color: "#b00020" }}>
                Video generation failed. Please try again.
              </div>
            )}
            {j.status === "done" && j.has_video && (
              <video controls preload="metadata" src={`/api/jobs/${j.id}/video`} style={{ maxWidth: "100%", maxHeight: 480, marginTop: 8 }} />
            )}
          </li>
        ))}
        {jobs.length === 0 && <li>No jobs yet.</li>}
      </ul>
    </main>
  );
}
