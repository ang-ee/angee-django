// Title: Gantt Skeleton
// Description: The gantt's first-load shape - a header band, lane labels and placeholder bars - shown until lanes arrive.

import { Skeleton, SkeletonStatus } from "../../ui/skeleton"

const LANES = [
  { label: "w-40", bars: [{ left: "6%", width: "28%" }] },
  { label: "w-32", bars: [{ left: "4%", width: "22%" }, { left: "30%", width: "26%" }] },
  { label: "w-24", bars: [{ left: "12%", width: "34%" }] },
  { label: "w-28", bars: [{ left: "2%", width: "18%" }, { left: "24%", width: "30%" }] },
  { label: "w-36", bars: [{ left: "18%", width: "24%" }] },
] as const

export function GanttSkeleton({
  label,
  treeWidth,
}: {
  label: string
  treeWidth: number
}) {
  return (
    <SkeletonStatus
      label={label}
      data-slot="gantt-loading"
      className="bg-sheet absolute inset-0 z-50 flex flex-col overflow-hidden"
    >
      <div aria-hidden className="flex flex-none border-b border-border" style={{ height: "4rem" }}>
        <div
          className="flex flex-none items-end border-r border-border px-4 pb-3"
          style={{ width: treeWidth }}
        >
          <Skeleton shape="text" size="sm" className="w-20" />
        </div>
        <div className="flex flex-1 items-end justify-between px-6 pb-3">
          {Array.from({ length: 6 }, (_, index) => (
            <Skeleton key={index} shape="text" size="sm" className="w-12" />
          ))}
        </div>
      </div>
      {LANES.map((lane, index) => (
        <div
          key={index}
          aria-hidden
          className="flex flex-none border-b border-border-subtle"
          style={{ height: "3.5rem" }}
        >
          <div
            className="flex flex-none items-center border-r border-border px-4"
            style={{ width: treeWidth }}
          >
            <Skeleton shape="text" size="md" className={lane.label} />
          </div>
          <div className="relative flex-1">
            {lane.bars.map((bar, barIndex) => (
              <Skeleton
                key={barIndex}
                className="absolute rounded-6"
                style={{ left: bar.left, width: bar.width, top: "1.125rem", height: "1.25rem" }}
              />
            ))}
          </div>
        </div>
      ))}
    </SkeletonStatus>
  )
}
