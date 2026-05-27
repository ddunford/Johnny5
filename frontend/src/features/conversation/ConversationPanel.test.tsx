import { afterEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/renderWithProviders";
import { useConsciousnessStore } from "@/stores/consciousnessStore";
import { ConversationPanel } from "./ConversationPanel";
import * as conversationService from "@/services/conversation";
import { ApiError } from "@/services/http";

vi.mock("@/services/conversation", async () => {
  const actual = await vi.importActual<typeof conversationService>("@/services/conversation");
  return { ...actual, sendMessage: vi.fn() };
});

const sendMessageMock = vi.mocked(conversationService.sendMessage);

afterEach(() => {
  sendMessageMock.mockReset();
});

function seedThought(id: number, text: string): void {
  act(() => {
    useConsciousnessStore.getState().addThought({ id, ts: "2026-05-20T09:05:00+00:00", text });
  });
}

describe("ConversationPanel", () => {
  it("renders the not-request/response explanation and an empty composer", () => {
    renderWithProviders(<ConversationPanel />);
    expect(screen.getByText(/he doesn.t reply on demand/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
  });

  it("blocks an empty message", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ConversationPanel />);
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Type something to say to him")).toBeInTheDocument();
    expect(sendMessageMock).not.toHaveBeenCalled();
  });

  it("acknowledges 'he heard you' on a 202 and surfaces thoughts since you spoke", async () => {
    const user = userEvent.setup();
    sendMessageMock.mockResolvedValue({ accepted: true, queue_depth: 2 });

    // A pre-existing thought marks "before you spoke".
    seedThought(5, "An older idle thought.");
    renderWithProviders(<ConversationPanel />);

    await user.type(screen.getByLabelText("Message to Johnny"), "What are you thinking about?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText(/he heard you/)).toBeInTheDocument();
    expect(screen.getByText(/queue 2/)).toBeInTheDocument();
    expect(sendMessageMock).toHaveBeenCalledWith("What are you thinking about?");

    // His reply emerges as a NEW thought on the stream a tick later.
    seedThought(6, "Dan just asked what I'm thinking about — I'm curious why now.");
    expect(
      await screen.findByText("Dan just asked what I'm thinking about — I'm curious why now."),
    ).toBeInTheDocument();
    // The older thought must NOT appear as a response (it predates the message).
    expect(screen.queryByText("An older idle thought.")).not.toBeInTheDocument();
  });

  it("shows a friendly error when the input queue is full (429)", async () => {
    const user = userEvent.setup();
    sendMessageMock.mockRejectedValue(new ApiError(429, "queue full"));
    renderWithProviders(<ConversationPanel />);

    await user.type(screen.getByLabelText("Message to Johnny"), "hello");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() =>
      expect(screen.getByText(/input queue full/i)).toBeInTheDocument(),
    );
  });
});
