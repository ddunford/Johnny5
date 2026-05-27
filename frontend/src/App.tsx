import type { ReactNode } from "react";
import { BrowserRouter } from "react-router-dom";
import { Providers } from "@/app/Providers";
import { AppRoutes } from "@/app/routes";

/**
 * Root composition. The token gate (TASK-5b.2) will wrap {@link AppRoutes} so the
 * panels only mount once a token is present; for now the shell + routes render
 * directly under the providers.
 */
export function App(): ReactNode {
  return (
    <Providers>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </Providers>
  );
}
