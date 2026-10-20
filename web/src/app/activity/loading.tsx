import { Bone, LoadingLabel, SkeletonPanel } from "@/components/Skeleton";

export default function Loading() {
  return (
    <div className="space-y-4">
      <LoadingLabel what="activity" />
      <h1 className="text-xl font-semibold tracking-tight">Activity</h1>
      <div className="flex flex-wrap gap-2">
        {Array.from({ length: 4 }, (_, i) => (
          <Bone key={i} className="h-7 w-28 rounded-full" />
        ))}
      </div>
      <SkeletonPanel rows={3} />
      <SkeletonPanel rows={6} />
      <SkeletonPanel rows={4} />
    </div>
  );
}
