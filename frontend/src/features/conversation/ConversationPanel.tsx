import type { ReactNode } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import { useConsciousnessStatus } from "@/hooks/streams";
import { formatTime } from "@/lib/format";
import { useConversation, type SentMessage } from "./useConversation";

const messageSchema = z.object({
  text: z.string().trim().min(1, "Type something to say to him"),
});

type MessageForm = z.infer<typeof messageSchema>;

function SentMessageRow({ message }: { message: SentMessage }): ReactNode {
  return (
    <li className={`convo-msg convo-msg--${message.status}`}>
      <p className="convo-msg__text">{message.text}</p>
      <p className="convo-msg__meta">
        <span>{formatTime(new Date(message.sentAt).toISOString())}</span>
        {message.status === "sending" ? <span>sending…</span> : null}
        {message.status === "heard" ? (
          <span className="convo-msg__heard">
            he heard you{typeof message.queueDepth === "number" ? ` · queue ${message.queueDepth}` : ""}
          </span>
        ) : null}
        {message.status === "error" ? (
          <span className="convo-msg__error" role="alert">
            {message.error}
          </span>
        ) : null}
      </p>
    </li>
  );
}

/**
 * Talk to Johnny. A message becomes a percept he thinks about — there is no reply
 * endpoint. After you speak, his stream-of-consciousness since that moment surfaces
 * below; his response emerges there (continuity, not request/response — SPEC §7).
 */
export function ConversationPanel(): ReactNode {
  const { messages, responses, isSending, send } = useConversation();
  const consciousnessStatus = useConsciousnessStatus();

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<MessageForm>({
    resolver: zodResolver(messageSchema),
    defaultValues: { text: "" },
  });

  const onSubmit = handleSubmit((values) => {
    send(values.text.trim());
    reset({ text: "" });
  });

  return (
    <section className="panel" aria-labelledby="conversation-heading">
      <div className="panel__titlebar">
        <h2 id="conversation-heading" className="panel__heading">
          Conversation
        </h2>
        <ConnectionBadge status={consciousnessStatus} />
      </div>

      <p className="convo-note">
        Speaking to Johnny gives him something to think about — he doesn&rsquo;t reply on demand.
        Watch his thoughts below: his response surfaces there a moment after you speak.
      </p>

      <form className="convo-form" onSubmit={onSubmit} noValidate>
        <label className="sr-only" htmlFor="convo-text">
          Message to Johnny
        </label>
        <textarea
          id="convo-text"
          className="convo-form__input"
          rows={2}
          placeholder="Say something to him…"
          aria-invalid={errors.text ? "true" : undefined}
          {...register("text")}
        />
        <button type="submit" className="convo-form__send" disabled={isSending}>
          {isSending ? "Sending…" : "Send"}
        </button>
      </form>
      {errors.text ? (
        <p className="convo-form__error" role="alert">
          {errors.text.message}
        </p>
      ) : null}

      {messages.length > 0 ? (
        <div className="convo-section">
          <h3 className="convo-subheading">You said</h3>
          <ul className="convo-list">
            {messages.map((message) => (
              <SentMessageRow key={message.localId} message={message} />
            ))}
          </ul>
        </div>
      ) : null}

      {messages.length > 0 ? (
        <div className="convo-section">
          <h3 className="convo-subheading">What he&rsquo;s thought since</h3>
          {responses.length === 0 ? (
            <p className="convo-waiting">He hasn&rsquo;t had a new thought yet — give him a tick.</p>
          ) : (
            <ul className="convo-responses">
              {responses.map((thought) => (
                <li key={thought.id} className="convo-response">
                  <span className="convo-response__time">{formatTime(thought.ts)}</span>
                  <span className="convo-response__text">{thought.text}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </section>
  );
}
