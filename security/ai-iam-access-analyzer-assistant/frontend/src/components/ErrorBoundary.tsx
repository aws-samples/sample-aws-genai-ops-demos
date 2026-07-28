import { Component, ReactNode } from "react";
import Alert from "@cloudscape-design/components/alert";
import Button from "@cloudscape-design/components/button";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      return (
        <Alert
          type="error"
          header="Something went wrong"
          action={
            <Button onClick={() => this.setState({ hasError: false, error: null })}>
              Try again
            </Button>
          }
        >
          {this.state.error?.message || "An unexpected error occurred while rendering this component."}
        </Alert>
      );
    }

    return this.props.children;
  }
}
