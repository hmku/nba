import { Bone, LoadingLabel, SkeletonPanel, SkeletonTitle } from "@/components/Skeleton";

export default function Loading() {
  return (
    <div className="space-y-4">
      <LoadingLabel what="inbox" />
      <SkeletonTitle title="Inbox" />
      <SkeletonPanel rows={6} className="border-accent/40" />
      <SkeletonPanel rows={3} />
      <div className="flex flex-wrap gap-2">
        {Array.from({ length: 4 }, (_, i) => (
          <Bone key={i} className="h-7 w-28 rounded-full" />
        ))}
      </div>
      <SkeletonPanel rows={6} />
    </div>
  );
}
