import type { ReactNode } from "react";
import { useToken } from "./useToken";
import { TokenEntryForm } from "./TokenEntryForm";

interface TokenGateProps {
  children: ReactNode;
}

/**
 * Gates the whole app on the shared token. With no token (fresh load, sign-out,
 * or a server rejection) it renders the entry form; once a token is present the
 * children (shell + panels) mount and start fetching. A 401/1008 anywhere clears
 * the token via {@link tokenStore.reject}, which re-renders this back to the gate.
 */
export function TokenGate({ children }: TokenGateProps): ReactNode {
  const { token, rejected, setToken } = useToken();

  if (!token) {
    return <TokenEntryForm rejected={rejected} onSubmit={setToken} />;
  }

  return <>{children}</>;
}
