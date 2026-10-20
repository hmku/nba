import { Bone, LoadingLabel, SkeletonPanel } from "@/components/Skeleton";

export default function Loading() {
  return (
    <>
      <LoadingLabel what="draft" />
      <div className="mb-3 flex gap-1 rounded-xl border border-line bg-panel p-1">
        {Array.from({ length: 3 }, (_, i) => (
          <Bone key={i} className="h-8 flex-1 rounded-lg" />
        ))}
      </div>
      <div className="space-y-3">
        <SkeletonPanel rows={5} />
        <SkeletonPanel rows={8} />
      </div>
    </>
  );
}
