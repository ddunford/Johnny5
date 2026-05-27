import type { ReactNode } from "react";
import { NavLink, Outlet } from "react-router-dom";

interface NavItem {
  to: string;
  label: string;
}

// The six SPEC §11 "views onto the same continuously-running being". The order
// leads with the live views (conversation, consciousness, state) then the
// archival ones (memory, audit, self).
const NAV: readonly NavItem[] = [
  { to: "/conversation", label: "Conversation" },
  { to: "/consciousness", label: "Consciousness" },
  { to: "/state", label: "State" },
  { to: "/memory", label: "Memory" },
  { to: "/audit", label: "Audit" },
  { to: "/self", label: "Self" },
];

interface AppShellProps {
  /** Slot for the token-gate control (sign-out / lock) — wired in TASK-5b.2. */
  headerActions?: ReactNode;
}

/**
 * App chrome: the header (identity + nav) and the routed panel outlet. Holds no
 * data of its own — every panel attaches to the headless Mind independently.
 */
export function AppShell({ headerActions }: AppShellProps): ReactNode {
  return (
    <div className="shell">
      <header className="shell__header">
        <div className="shell__brand">
          <span className="shell__brand-name">Johnny&nbsp;5</span>
          <span className="shell__brand-sub">a window onto him</span>
        </div>
        <nav className="shell__nav" aria-label="Panels">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                isActive ? "shell__nav-link shell__nav-link--active" : "shell__nav-link"
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        {headerActions ? <div className="shell__actions">{headerActions}</div> : null}
      </header>
      <main className="shell__main">
        <Outlet />
      </main>
    </div>
  );
}
