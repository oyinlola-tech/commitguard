import { Component, type ErrorInfo, type ReactNode } from "react";

import { ErrorState } from "./States";

/** Contains a rendering failure to one page instead of blanking the application. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { failed: boolean }> {
  override state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // Only the error type is logged: messages may contain untrusted data.
    console.error("dashboard_render_error", error.name, info.componentStack?.split("\n")[1]?.trim());
  }

  override componentDidUpdate(previous: { resetKey?: string }): void {
    if (previous.resetKey !== this.props.resetKey && this.state.failed) this.setState({ failed: false });
  }

  override render(): ReactNode {
    if (this.state.failed) {
      return <ErrorState title="Something went wrong." onRetry={() => this.setState({ failed: false })} />;
    }
    return this.props.children;
  }
}
