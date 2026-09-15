import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute("data-theme");
});

// jsdom has no layout: provide the browser APIs components touch.
Object.defineProperty(window, "scrollTo", { value: () => undefined, writable: true });
