import type { ReactNode } from "react";
import { BrowserRouter } from "react-router-dom";
import { Providers } from "@/app/Providers";
import { AppRoutes } from "@/app/routes";
import { TokenGate } from "@/auth/TokenGate";
import { SignOutButton } from "@/auth/SignOutButton";
import { LiveStreams } from "@/components/LiveStreams";

/**
 * Root composition. The token gate wraps the routed app, so the shell + panels
 * only mount once a token is present; a 401/1008 anywhere bounces back here.
 * {@link LiveStreams} opens the WS streams for the whole authenticated session.
 */
export function App(): ReactNode {
  return (
    <Providers>
      <TokenGate>
        <LiveStreams />
        <BrowserRouter>
          <AppRoutes headerActions={<SignOutButton />} />
        </BrowserRouter>
      </TokenGate>
    </Providers>
  );
}
