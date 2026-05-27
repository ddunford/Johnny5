import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { ErrorBoundary } from "./ErrorBoundary";

function Bomb({ explode }: { explode: boolean }): React.ReactNode {
  if (explode) {
    throw new Error("kaboom");
  }
  return <p>alive</p>;
}

describe("ErrorBoundary", () => {
  it("renders children when nothing throws", () => {
    render(
      <ErrorBoundary name="Test">
        <p>healthy</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText("healthy")).toBeInTheDocument();
  });

  it("catches a thrown render and shows the named fallback", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary name="Memory">
        <Bomb explode />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Memory");
    expect(screen.getByText("kaboom")).toBeInTheDocument();
  });

  it("recovers after Retry once the child stops throwing", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const user = userEvent.setup();

    function Harness(): React.ReactNode {
      const [explode, setExplode] = useState(true);
      return (
        <>
          <button type="button" onClick={() => setExplode(false)}>
            defuse
          </button>
          <ErrorBoundary name="Panel">
            <Bomb explode={explode} />
          </ErrorBoundary>
        </>
      );
    }

    render(<Harness />);
    expect(screen.getByRole("alert")).toBeInTheDocument();

    // Stop the child throwing, then reset the boundary.
    await user.click(screen.getByText("defuse"));
    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(screen.getByText("alive")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
