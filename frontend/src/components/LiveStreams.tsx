import { useLiveStreams } from "@/hooks/streams";

/**
 * Side-effect-only component: opens the consciousness + state WS streams for the
 * whole authenticated session. Renders nothing. Mounted inside the token gate so
 * the streams open once a token exists and close on detach.
 */
export function LiveStreams(): null {
  useLiveStreams();
  return null;
}
