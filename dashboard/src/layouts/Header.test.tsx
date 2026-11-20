import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { setPreference } = vi.hoisted(() => ({ setPreference: vi.fn() }));

vi.mock("../context/ThemeContext", () => ({
  useTheme: () => ({ isDark: false, preference: "light", setPreference }),
}));

vi.mock("../components/PwaInstallPrompt", () => ({
  default: () => null,
}));

vi.mock("../components/AppVersionBadge", () => ({
  default: () => null,
}));

vi.mock("../components/CurrentVersionBadge", () => ({
  default: () => null,
}));

import Header from "./Header";

describe("Header", () => {
  it("pads mobile chrome for iOS PWA safe-area inset", () => {
    render(<Header isMobile onToggle={() => undefined} />);
    const header = screen.getByRole("banner");
    expect(header.style.height).toContain("safe-area-inset-top");
    expect(header.style.padding).toContain("safe-area-inset-top");
  });

  it("keeps the status-bar inset opaque so iOS does not frost page content", () => {
    render(<Header isMobile onToggle={() => undefined} />);
    const header = screen.getByRole("banner");
    expect(header.style.backdropFilter).toBe("none");
    expect(header.style.WebkitBackdropFilter).toBe("none");
  });

  it("exposes the current theme and switches to dark from the desktop header", () => {
    render(<Header isMobile={false} />);
    expect(
      screen.getByRole("button", { name: "header.themeLight" }),
    ).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "header.themeDark" }));
    expect(setPreference).toHaveBeenCalledWith("dark");
  });
});
