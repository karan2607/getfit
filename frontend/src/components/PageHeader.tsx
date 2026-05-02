interface PageHeaderProps {
  title: string
  subtitle?: string
  action?: React.ReactNode
}

export default function PageHeader({ title, subtitle, action }: PageHeaderProps) {
  return (
    <div className="bg-rose-700 px-6 py-5 flex items-start justify-between gap-4">
      <div>
        <h1 className="text-2xl font-extrabold tracking-tight text-white leading-tight">{title}</h1>
        {subtitle && <p className="text-sm text-rose-200 mt-0.5">{subtitle}</p>}
      </div>
      {action && <div className="flex-shrink-0 mt-0.5">{action}</div>}
    </div>
  )
}
