import { useEffect, useId, useRef, type ReactNode } from 'react'

// Shared modal shell. Every overlay in the app was previously a bare
// `fixed inset-0` div: a screen reader announced nothing, Escape did nothing,
// and Tab walked straight out of the dialog into the page behind it.
//
// Focus is moved into the dialog on open, trapped while it is open, and
// returned to whatever was focused before -- that last part is what stops the
// keyboard user being dumped at the top of the document on close.

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

interface ModalOverlayProps {
  /** Accessible name. Rendered visually only if `titleVisible` markup is supplied by the caller. */
  label: string
  onClose?: () => void
  /** Clicking the backdrop closes, matching the existing drawer behaviour. */
  closeOnBackdrop?: boolean
  className?: string
  children: ReactNode
}

export function ModalOverlay({
  label,
  onClose,
  closeOnBackdrop = false,
  className = 'grid place-items-center bg-black/70 p-4 backdrop-blur-sm',
  children,
}: ModalOverlayProps) {
  const dialogRef = useRef<HTMLDivElement | null>(null)
  const labelId = useId()

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) {
      return
    }

    const previouslyFocused = document.activeElement as HTMLElement | null
    const focusable = () => Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE))

    // Prefer a real control; fall back to the dialog itself so focus is at
    // least inside the dialog rather than left on the page behind it.
    const first = focusable()[0]
    if (first) {
      first.focus()
    } else {
      dialog.focus()
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && onClose) {
        event.stopPropagation()
        onClose()
        return
      }

      if (event.key !== 'Tab') {
        return
      }

      const items = focusable()
      if (items.length === 0) {
        event.preventDefault()
        return
      }

      const firstItem = items[0]
      const lastItem = items[items.length - 1]
      const active = document.activeElement

      if (event.shiftKey && (active === firstItem || !dialog.contains(active))) {
        event.preventDefault()
        lastItem.focus()
      } else if (!event.shiftKey && active === lastItem) {
        event.preventDefault()
        firstItem.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown, true)
    return () => {
      document.removeEventListener('keydown', handleKeyDown, true)
      previouslyFocused?.focus?.()
    }
  }, [onClose])

  return (
    <div
      className={`fixed inset-0 z-50 flex ${className}`}
      onClick={closeOnBackdrop && onClose ? onClose : undefined}
    >
      <span id={labelId} className="sr-only">
        {label}
      </span>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelId}
        tabIndex={-1}
        onClick={event => event.stopPropagation()}
        className="contents outline-none"
      >
        {children}
      </div>
    </div>
  )
}
