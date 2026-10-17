import { Component, type ErrorInfo, type ReactNode } from 'react'

/** Keeps one broken page from blanking the whole app. Give it `key={route}` to reset per page. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('page crashed', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <section>
          <h1>Something went wrong</h1>
          <p className="error">{this.state.error.message}</p>
          <p className="muted">Use the 💬 Feedback button to tell us what you were doing.</p>
        </section>
      )
    }
    return this.props.children
  }
}
