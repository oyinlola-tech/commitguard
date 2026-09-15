import "@testing-library/jest-dom/vitest";

import { cleanup, configure } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// Lazy routes and polling make first renders slower on a loaded CI machine.
configure({ asyncUtilTimeout: 5_000 });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute("data-theme");
});

// jsdom has no layout: provide the browser APIs components touch.
Object.defineProperty(window, "scrollTo", { value: () => undefined, writable: true });
