import { LoadingLabel, SkeletonPanel, SkeletonTitle } from "@/components/Skeleton";

export default function Loading() {
  return (
    <div className="space-y-3">
      <LoadingLabel what="league" />
      <SkeletonTitle title="League" />
      <SkeletonPanel rows={12} />
      <SkeletonPanel rows={4} />
    </div>
  );
}
