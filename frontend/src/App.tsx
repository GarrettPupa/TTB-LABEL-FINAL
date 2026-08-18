import { useEffect, useState } from "react";

type HealthResponse = { status: string };

export function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/health")
      .then((response) => {
        if (!response.ok) throw new Error(`Request failed (${response.status})`);
        return response.json() as Promise<HealthResponse>;
      })
      .then(setHealth)
      .catch(() => setError("The backend health check is unavailable."));
  }, []);

  return (
    <main>
      <p className="eyebrow">TTB LABEL VERIFICATION</p>
      <h1>Phase 0 is live.</h1>
      <p className="lede">A small, ready-to-grow foundation for label review.</p>
      <section className="status" aria-live="polite">
        <span className={`dot ${health ? "online" : ""}`} />
        <div>
          <p className="label">Backend status</p>
          <strong>{health ? health.status : error ?? "checking..."}</strong>
        </div>
      </section>
    </main>
  );
}
