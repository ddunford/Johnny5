import { describe, expect, it } from "vitest";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TokenGate } from "./TokenGate";
import { reject } from "./tokenStore";

function Protected(): React.ReactNode {
  return <p>secret panels</p>;
}

describe("TokenGate", () => {
  it("shows the entry form when no token is present", () => {
    render(
      <TokenGate>
        <Protected />
      </TokenGate>,
    );
    expect(screen.getByLabelText("Access token")).toBeInTheDocument();
    expect(screen.queryByText("secret panels")).not.toBeInTheDocument();
  });

  it("rejects a blank token with a validation message", async () => {
    const user = userEvent.setup();
    render(
      <TokenGate>
        <Protected />
      </TokenGate>,
    );
    await user.click(screen.getByRole("button", { name: "Attach" }));
    expect(await screen.findByText("Enter the access token")).toBeInTheDocument();
    expect(screen.queryByText("secret panels")).not.toBeInTheDocument();
  });

  it("mounts the children once a token is entered and stores it in sessionStorage", async () => {
    const user = userEvent.setup();
    render(
      <TokenGate>
        <Protected />
      </TokenGate>,
    );
    await user.type(screen.getByLabelText("Access token"), "s3cret-token");
    await user.click(screen.getByRole("button", { name: "Attach" }));

    expect(await screen.findByText("secret panels")).toBeInTheDocument();
    expect(window.sessionStorage.getItem("johnny5.token")).toBe("s3cret-token");
    expect(window.localStorage.getItem("johnny5.token")).toBeNull();
  });

  it("returns to the gate with an error when the token is rejected (401/1008)", async () => {
    const user = userEvent.setup();
    render(
      <TokenGate>
        <Protected />
      </TokenGate>,
    );
    await user.type(screen.getByLabelText("Access token"), "wrong");
    await user.click(screen.getByRole("button", { name: "Attach" }));
    expect(await screen.findByText("secret panels")).toBeInTheDocument();

    // Simulate the REST/WS layer rejecting the token.
    act(() => reject());

    expect(await screen.findByText("That token was rejected. Try again.")).toBeInTheDocument();
    expect(screen.queryByText("secret panels")).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem("johnny5.token")).toBeNull();
  });
});
