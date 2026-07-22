"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage, readDevIdentity } from "../lib";

type MembershipRole = "owner" | "manager" | "operator" | "content_editor" | "auditor";
type MembershipStatus = "active" | "inactive";

type StaffMember = {
  id: string;
  user_id: string;
  role: MembershipRole;
  status: MembershipStatus;
  created_at: string;
};

type StaffList = {
  items: StaffMember[];
  total: number;
  limit: number;
  offset: number;
};

type ListState =
  | { kind: "no-identity" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; list: StaffList; viewerRole: MembershipRole };

type ClinicContext = { role: MembershipRole };

const ROLES: MembershipRole[] = ["owner", "manager", "operator", "content_editor", "auditor"];
const PAGE_SIZE = 20;
// Hides the mutation controls entirely for a role that can never succeed at
// them - a usability convenience only. The backend independently enforces
// every rule regardless of what the UI offers.
const MANAGE_ROLES = new Set<MembershipRole>(["owner", "manager"]);

export default function StaffPage() {
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [roleFilter, setRoleFilter] = useState<MembershipRole | "">("");
  const [statusFilter, setStatusFilter] = useState<MembershipStatus | "">("");
  const [offset, setOffset] = useState(0);

  const [newUserId, setNewUserId] = useState("");
  const [newRole, setNewRole] = useState<MembershipRole>("operator");
  const [addSubmitting, setAddSubmitting] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const [rowError, setRowError] = useState<string | null>(null);
  const [pendingRowId, setPendingRowId] = useState<string | null>(null);

  const load = useCallback(() => {
    const identity = readDevIdentity();
    if (!identity) {
      setState({ kind: "no-identity" });
      return;
    }
    setState({ kind: "loading" });
    const params = new URLSearchParams();
    params.set("limit", String(PAGE_SIZE));
    params.set("offset", String(offset));
    if (roleFilter) params.set("role", roleFilter);
    if (statusFilter) params.set("status", statusFilter);

    Promise.all([
      apiFetch<StaffList>(identity, `/api/v1/clinic/staff?${params.toString()}`),
      apiFetch<ClinicContext>(identity, "/api/v1/clinic"),
    ])
      .then(([list, clinic]) => setState({ kind: "loaded", list, viewerRole: clinic.role }))
      .catch((error: unknown) => setState({ kind: "error", message: errorMessage(error) }));
  }, [offset, roleFilter, statusFilter]);

  useEffect(() => {
    // Deferred via a microtask rather than called directly: `load` itself
    // calls setState synchronously before its first `await`, and doing that
    // straight from an effect body trips react-hooks/set-state-in-effect.
    queueMicrotask(load);
    window.addEventListener("dev-identity-changed", load);
    return () => window.removeEventListener("dev-identity-changed", load);
  }, [load]);

  async function handleAddMember(e: React.FormEvent) {
    e.preventDefault();
    const identity = readDevIdentity();
    if (!identity || !newUserId.trim()) {
      return;
    }
    setAddSubmitting(true);
    setAddError(null);
    try {
      await apiFetch(identity, "/api/v1/clinic/staff", {
        method: "POST",
        body: JSON.stringify({ user_id: newUserId.trim(), role: newRole }),
      });
      setNewUserId("");
      load();
    } catch (error: unknown) {
      setAddError(errorMessage(error));
    } finally {
      setAddSubmitting(false);
    }
  }

  async function handleRoleChange(member: StaffMember, role: MembershipRole) {
    if (role === member.role) return;
    if (!window.confirm(`Change role of ${member.user_id} from ${member.role} to ${role}?`)) {
      return;
    }
    await runRowAction(member.id, { role });
  }

  async function handleToggleStatus(member: StaffMember) {
    const nextStatus: MembershipStatus = member.status === "active" ? "inactive" : "active";
    const verb = nextStatus === "inactive" ? "deactivate" : "activate";
    if (!window.confirm(`${verb} membership for ${member.user_id}?`)) {
      return;
    }
    await runRowAction(member.id, { status: nextStatus });
  }

  async function handleRemove(member: StaffMember) {
    if (!window.confirm(`Remove ${member.user_id} from this clinic's staff?`)) {
      return;
    }
    const identity = readDevIdentity();
    if (!identity) return;
    setPendingRowId(member.id);
    setRowError(null);
    try {
      await apiFetch(identity, `/api/v1/clinic/staff/${member.id}`, { method: "DELETE" });
      load();
    } catch (error: unknown) {
      setRowError(errorMessage(error));
    } finally {
      setPendingRowId(null);
    }
  }

  async function runRowAction(
    membershipId: string,
    payload: { role?: MembershipRole; status?: MembershipStatus },
  ) {
    const identity = readDevIdentity();
    if (!identity) return;
    setPendingRowId(membershipId);
    setRowError(null);
    try {
      await apiFetch(identity, `/api/v1/clinic/staff/${membershipId}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      load();
    } catch (error: unknown) {
      setRowError(errorMessage(error));
    } finally {
      setPendingRowId(null);
    }
  }

  if (state.kind === "no-identity") {
    return <p>Set a development identity above to view staff.</p>;
  }
  if (state.kind === "loading") {
    return <p>Loading staff…</p>;
  }
  if (state.kind === "error") {
    return <p>Could not load staff: {state.message}</p>;
  }

  const { list, viewerRole } = state;
  const canManage = MANAGE_ROLES.has(viewerRole);

  return (
    <section>
      <h2>Staff</h2>

      <div style={{ display: "flex", gap: "1rem", marginBottom: "1rem" }}>
        <label>
          Role filter{" "}
          <select
            value={roleFilter}
            onChange={(e) => {
              setOffset(0);
              setRoleFilter(e.target.value as MembershipRole | "");
            }}
          >
            <option value="">All</option>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <label>
          Status filter{" "}
          <select
            value={statusFilter}
            onChange={(e) => {
              setOffset(0);
              setStatusFilter(e.target.value as MembershipStatus | "");
            }}
          >
            <option value="">All</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>
        </label>
      </div>

      {rowError && <p role="alert">{rowError}</p>}

      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr>
            <th style={{ textAlign: "left" }}>User</th>
            <th style={{ textAlign: "left" }}>Role</th>
            <th style={{ textAlign: "left" }}>Status</th>
            <th style={{ textAlign: "left" }}>Created</th>
            <th style={{ textAlign: "left" }}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {list.items.map((member) => (
            <tr key={member.id}>
              <td>{member.user_id}</td>
              <td>
                {canManage ? (
                  <select
                    value={member.role}
                    disabled={pendingRowId === member.id}
                    onChange={(e) => handleRoleChange(member, e.target.value as MembershipRole)}
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>
                ) : (
                  member.role
                )}
              </td>
              <td>{member.status}</td>
              <td>{new Date(member.created_at).toLocaleDateString()}</td>
              <td style={{ display: "flex", gap: "0.5rem" }}>
                {canManage ? (
                  <>
                    <button
                      type="button"
                      disabled={pendingRowId === member.id}
                      onClick={() => handleToggleStatus(member)}
                    >
                      {member.status === "active" ? "Deactivate" : "Activate"}
                    </button>
                    <button
                      type="button"
                      disabled={pendingRowId === member.id}
                      onClick={() => handleRemove(member)}
                    >
                      Remove
                    </button>
                  </>
                ) : (
                  <span>—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div style={{ display: "flex", gap: "1rem", marginTop: "1rem", alignItems: "center" }}>
        <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
          Previous
        </button>
        <span>
          {list.total === 0
            ? "No staff"
            : `${offset + 1}-${Math.min(offset + PAGE_SIZE, list.total)} of ${list.total}`}
        </span>
        <button type="button" disabled={offset + PAGE_SIZE >= list.total} onClick={() => setOffset(offset + PAGE_SIZE)}>
          Next
        </button>
      </div>

      {canManage && (
        <>
          <h3 style={{ marginTop: "2rem" }}>Add staff member</h3>
          <p>
            This provisions a membership for an existing development/test user id - there is no
            email-invitation delivery system in this foundation slice (see ARCHITECTURE.md).
          </p>
          <form
            onSubmit={handleAddMember}
            style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}
          >
            <input
              aria-label="New staff member user id"
              placeholder="User id (UUID)"
              value={newUserId}
              onChange={(e) => setNewUserId(e.target.value)}
              disabled={addSubmitting}
              style={{ padding: "0.25rem", minWidth: 280 }}
            />
            <select
              value={newRole}
              onChange={(e) => setNewRole(e.target.value as MembershipRole)}
              disabled={addSubmitting}
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <button type="submit" disabled={addSubmitting || !newUserId.trim()}>
              {addSubmitting ? "Adding…" : "Add"}
            </button>
          </form>
          {addError && <p role="alert">{addError}</p>}
        </>
      )}
    </section>
  );
}
