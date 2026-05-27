import { useRef, useState } from "react";
import { useSendMessage } from "@/hooks/useSendMessage";
import { useThoughtsFeed } from "@/hooks/streams";
import { ApiError } from "@/services/http";
import type { Thought } from "@/services/types";

export type SentStatus = "sending" | "heard" | "error";

export interface SentMessage {
  localId: number;
  text: string;
  sentAt: number;
  status: SentStatus;
  queueDepth?: number;
  error?: string;
}

export interface UseConversation {
  messages: SentMessage[];
  /** Thoughts that have emerged since your most recent message (his response surfaces here). */
  responses: Thought[];
  isSending: boolean;
  send: (text: string) => void;
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 413) {
      return "That message is too long for him to take in.";
    }
    if (error.status === 429) {
      return "He's saturated right now (input queue full) — give him a moment.";
    }
    return error.message;
  }
  return "Couldn't reach him — check the connection.";
}

/**
 * Conversation state. Sending is NOT request/response: `POST /input` only returns
 * a 202 ack `{accepted,queue_depth}`. His reply emerges as a thought on the live
 * consciousness stream a tick later (SPEC §7), so we mark the moment you spoke (by
 * the latest thought id) and surface every thought that arrives after it — no hung
 * "awaiting reply" spinner.
 */
export function useConversation(): UseConversation {
  const feed = useThoughtsFeed();
  const { mutate, isPending } = useSendMessage();
  const [messages, setMessages] = useState<SentMessage[]>([]);
  const [watchFromId, setWatchFromId] = useState<number | null>(null);
  const nextLocalId = useRef(0);

  const send = (text: string): void => {
    const localId = nextLocalId.current++;
    // The latest thought id right now marks "before you spoke"; thoughts after it
    // are what he's thought since (his response is somewhere in there).
    const lastId = feed.length > 0 ? feed[feed.length - 1].id : 0;

    setMessages((prev) => [
      ...prev,
      { localId, text, sentAt: Date.now(), status: "sending" },
    ]);

    mutate(text, {
      onSuccess: (ack) => {
        setWatchFromId(lastId);
        setMessages((prev) =>
          prev.map((message) =>
            message.localId === localId
              ? { ...message, status: "heard", queueDepth: ack.queue_depth }
              : message,
          ),
        );
      },
      onError: (error) => {
        setMessages((prev) =>
          prev.map((message) =>
            message.localId === localId
              ? { ...message, status: "error", error: describeError(error) }
              : message,
          ),
        );
      },
    });
  };

  const responses = watchFromId === null ? [] : feed.filter((thought) => thought.id > watchFromId);

  return { messages, responses, isSending: isPending, send };
}
