/**
 * Mutation hook for sending Johnny a message. The result is only the 202 ack
 * (`{accepted,queue_depth}`) — NOT a reply. His reply surfaces as a thought on
 * `/ws/consciousness`, so the conversation panel correlates by timestamp rather
 * than awaiting a synchronous response (no "awaiting reply" spinner).
 */

import { useMutation, type UseMutationResult } from "@tanstack/react-query";
import { sendMessage } from "@/services/conversation";
import type { InputAck } from "@/services/types";

export function useSendMessage(): UseMutationResult<InputAck, Error, string> {
  return useMutation({
    mutationFn: (text: string) => sendMessage(text),
  });
}
