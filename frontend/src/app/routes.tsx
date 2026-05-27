import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { PanelBoundary } from "@/components/PanelBoundary";
import { ConversationPanel } from "@/features/conversation/ConversationPanel";
import { ConsciousnessPanel } from "@/features/consciousness/ConsciousnessPanel";
import { StatePanel } from "@/features/state/StatePanel";
import { MemoryPanel } from "@/features/memory/MemoryPanel";
import { AuditPanel } from "@/features/audit/AuditPanel";
import { SelfPanel } from "@/features/self/SelfPanel";

interface PanelRoute {
  path: string;
  name: string;
  element: ReactNode;
}

// Each panel is wrapped in its own error boundary at the route level, so a crash
// in one never blanks the shell or the other panels.
const PANELS: readonly PanelRoute[] = [
  { path: "conversation", name: "Conversation", element: <ConversationPanel /> },
  { path: "consciousness", name: "Consciousness", element: <ConsciousnessPanel /> },
  { path: "state", name: "State", element: <StatePanel /> },
  { path: "memory", name: "Memory", element: <MemoryPanel /> },
  { path: "audit", name: "Audit", element: <AuditPanel /> },
  { path: "self", name: "Self", element: <SelfPanel /> },
];

export function AppRoutes({ headerActions }: { headerActions?: ReactNode }): ReactNode {
  return (
    <Routes>
      <Route element={<AppShell headerActions={headerActions} />}>
        <Route index element={<Navigate to="/consciousness" replace />} />
        {PANELS.map((panel) => (
          <Route
            key={panel.path}
            path={panel.path}
            element={<PanelBoundary name={panel.name}>{panel.element}</PanelBoundary>}
          />
        ))}
        <Route path="*" element={<Navigate to="/consciousness" replace />} />
      </Route>
    </Routes>
  );
}
