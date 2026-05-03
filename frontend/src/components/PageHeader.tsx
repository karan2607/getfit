interface PageHeaderProps {
  title: string
  subtitle?: string
  action?: React.ReactNode
}

export default function PageHeader({ title, subtitle, action }: PageHeaderProps) {
  return (
    <div className="bg-brand-500 px-4 md:px-6 py-4 md:py-5 flex items-start justify-between gap-3">
      <div>
        <h1 className="text-xl md:text-2xl font-extrabold tracking-tight text-white leading-tight">{title}</h1>
        {subtitle && <p className="text-sm text-white/75 mt-0.5">{subtitle}</p>}
      </div>
      {action && <div className="flex-shrink-0 mt-0.5">{action}</div>}
    </div>
  )
}
