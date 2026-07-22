import Link from "next/link";
import { IdentityBanner } from "./identity-banner";

export default function SettingsLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 900 }}>
      <h1>Clinic administration</h1>
      <nav style={{ display: "flex", gap: "1rem", marginBottom: "1rem" }}>
        <Link href="/settings/clinic">Clinic settings</Link>
        <Link href="/settings/staff">Staff</Link>
      </nav>
      <IdentityBanner />
      {children}
    </main>
  );
}
