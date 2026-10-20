import { Bone, LoadingLabel, SkeletonPanel, SkeletonTitle } from "@/components/Skeleton";

export default function Loading() {
  return (
    <div className="space-y-3">
      <LoadingLabel what="lineup" />
      <div className="flex gap-1.5 overflow-hidden pb-1">
        {Array.from({ length: 10 }, (_, i) => (
          <Bone key={i} className="h-11 w-12 shrink-0 rounded-lg" />
        ))}
      </div>
      <SkeletonTitle title="Lineup" />
      <Bone className="h-10 w-full rounded-xl" />
      <SkeletonPanel rows={12} />
      <SkeletonPanel rows={5} />
    </div>
  );
}
