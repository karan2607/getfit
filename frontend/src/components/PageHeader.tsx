import NotificationBell from './NotificationBell'

interface PageHeaderProps {
  title: string
  subtitle?: string
  action?: React.ReactNode
}

export default function PageHeader({ title, subtitle, action }: PageHeaderProps) {
  return (
    <div className="sticky top-0 z-20 bg-brand-500 px-4 md:px-6 py-4 md:py-5 flex items-start justify-between gap-3">
      <div>
        <h1 className="text-xl md:text-2xl font-extrabold tracking-tight text-white leading-tight">{title}</h1>
        {subtitle && <p className="text-sm text-white/75 mt-0.5">{subtitle}</p>}
      </div>
      {/* The bell lives in the header itself rather than being passed per
          page, so it is on every screen without each page remembering to
          add it. It renders nothing for ordinary users.

          It is deliberately LAST. Its panel is anchored with `right-0`,
          which means the right edge of the bell -- so with a page action
          beside it, the panel opened from a point inset from the header
          edge and ran off under that action. On Dashboard, where the bell
          happened to be rightmost already, it looked fine, which is why
          this only showed up on other pages. Keep the bell last and the
          anchor and the header edge are the same line. */}
      <div className="flex-shrink-0 mt-0.5 flex items-center gap-1">
        {action}
        <NotificationBell />
      </div>
    </div>
  )
}
