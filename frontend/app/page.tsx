import { HealthStatus } from "./health-status";

export default function Home() {
  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 640 }}>
      <h1>Clinic Admin Platform</h1>
      <p>Development foundation — no business functionality yet.</p>
      <HealthStatus />
    </main>
  );
}
