import { Bone, LoadingLabel, SkeletonPanel } from "@/components/Skeleton";

export default function Loading() {
  return (
    <div className="space-y-3">
      <LoadingLabel what="matchup" />
      <div className="flex gap-1.5 overflow-hidden pb-1">
        {Array.from({ length: 10 }, (_, i) => (
          <Bone key={i} className="h-11 w-12 shrink-0 rounded-lg" />
        ))}
      </div>
      <div className="rounded-xl border border-line bg-panel p-4">
        <Bone className="h-4 w-48" />
        <Bone className="mt-3 h-9 w-32" />
        <Bone className="mt-3 h-3 w-full" />
      </div>
      <SkeletonPanel rows={9} />
      <SkeletonPanel rows={5} />
    </div>
  );
}
