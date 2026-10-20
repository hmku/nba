import { Bone, LoadingLabel, SkeletonPanel } from "@/components/Skeleton";

export default function Loading() {
  return (
    <div className="space-y-3">
      <LoadingLabel what="this week" />
      <div className="flex gap-1.5 overflow-hidden pb-1">
        {Array.from({ length: 10 }, (_, i) => (
          <Bone key={i} className="h-11 w-12 shrink-0 rounded-lg" />
        ))}
      </div>
      <div className="rounded-xl border border-line bg-panel p-4">
        <Bone className="h-3 w-48" />
        <div className="mt-3 flex items-center justify-between gap-3">
          <Bone className="h-5 w-28" />
          <Bone className="h-8 w-16" />
          <Bone className="h-5 w-28" />
        </div>
        <Bone className="mt-3 h-3 w-full" />
      </div>
      <SkeletonPanel rows={9} head={false} />
      <div className="grid gap-3 md:grid-cols-2">
        <SkeletonPanel rows={10} />
        <SkeletonPanel rows={10} className="hidden md:block" />
      </div>
      <SkeletonPanel rows={3} />
    </div>
  );
}
