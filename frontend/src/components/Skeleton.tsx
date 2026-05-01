function SkeletonLine({ w = 'w-full', h = 'h-4' }: { w?: string; h?: string }) {
  return <div className={`${w} ${h} bg-gray-200 rounded animate-pulse`} />
}

export function SkeletonCard() {
  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-5 space-y-3">
      <SkeletonLine w="w-2/5" h="h-5" />
      <SkeletonLine w="w-3/4" h="h-3" />
      <div className="flex gap-2 pt-1">
        <SkeletonLine w="w-16" h="h-3" />
        <SkeletonLine w="w-20" h="h-3" />
      </div>
    </div>
  )
}

export function SkeletonRow() {
  return (
    <div className="flex items-center gap-3 px-4 py-3 bg-white rounded-xl border border-gray-100">
      <div className="w-9 h-9 rounded-full bg-gray-200 animate-pulse flex-shrink-0" />
      <div className="flex-1 space-y-2">
        <SkeletonLine w="w-2/3" h="h-4" />
        <SkeletonLine w="w-1/2" h="h-3" />
      </div>
    </div>
  )
}

export function SkeletonText({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: lines }).map((_, i) => (
        <SkeletonLine key={i} w={i === lines - 1 ? 'w-3/4' : 'w-full'} />
      ))}
    </div>
  )
}
