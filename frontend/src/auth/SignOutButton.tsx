import type { ReactNode } from "react";
import { useToken } from "./useToken";

/**
 * Header action: drop the token (sessionStorage cleared) and return to the gate.
 * "Detach" rather than "sign out" — there is no user session, only a window onto
 * the running Mind.
 */
export function SignOutButton(): ReactNode {
  const { clearToken } = useToken();
  return (
    <button type="button" className="shell__detach" onClick={clearToken}>
      Detach
    </button>
  );
}
