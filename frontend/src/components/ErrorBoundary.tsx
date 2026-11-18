import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { RotateCcw, TriangleAlert } from 'lucide-react'

interface ErrorBoundaryState {
  hasError: boolean
  message: string
}

export class ErrorBoundary extends Component<{ children: ReactNode }, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false, message: '' }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, message: error.message || 'Terjadi kesalahan saat memuat tampilan.' }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('UI ErrorBoundary caught:', error, info.componentStack)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="boot-screen" role="alert">
          <TriangleAlert size={30} />
          <h2>Tampilan gagal dimuat</h2>
          <p>{this.state.message}</p>
          <button
            type="button"
            className="button button--primary"
            onClick={() => window.location.reload()}
          >
            <RotateCcw size={16} />Muat Ulang Halaman
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
