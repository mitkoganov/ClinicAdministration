"use client";

import { useEffect, useState } from "react";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type HealthState =
  | { kind: "loading" }
  | { kind: "ok"; status: string }
  | { kind: "error"; message: string };

export function HealthStatus() {
  const [health, setHealth] = useState<HealthState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;

    fetch(`${API_BASE_URL}/health`)
      .then((res) => {
        if (!res.ok) {
          throw new Error(`backend responded with ${res.status}`);
        }
        return res.json() as Promise<{ status: string }>;
      })
      .then((data) => {
        if (!cancelled) {
          setHealth({ kind: "ok", status: data.status });
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          const message = error instanceof Error ? error.message : "unknown error";
          setHealth({ kind: "error", message });
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section>
      <h2>Backend health</h2>
      <p>API base URL: {API_BASE_URL}</p>
      {health.kind === "loading" && <p>Checking backend health…</p>}
      {health.kind === "ok" && <p>Status: {health.status}</p>}
      {health.kind === "error" && <p>Backend unreachable: {health.message}</p>}
    </section>
  );
}
