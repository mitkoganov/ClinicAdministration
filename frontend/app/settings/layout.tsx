import Link from "next/link";
import { IdentityBanner } from "./identity-banner";
import { SessionStatus } from "./session-status";

export default function SettingsLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 900 }}>
      <h1>Clinic administration</h1>
      <SessionStatus />
      <nav style={{ display: "flex", gap: "1rem", marginBottom: "1rem", flexWrap: "wrap" }}>
        <Link href="/settings/clinic">Clinic settings</Link>
        <Link href="/settings/staff">Staff</Link>
        <Link href="/settings/security">Security</Link>
        <Link href="/calendar">Calendar</Link>
        <Link href="/settings/rooms">Rooms</Link>
        <Link href="/settings/service-types">Service types</Link>
        <Link href="/settings/schedules">Provider schedules</Link>
        <Link href="/settings/blocks">Calendar blocks</Link>
      </nav>
      <IdentityBanner />
      {children}
    </main>
  );
}
