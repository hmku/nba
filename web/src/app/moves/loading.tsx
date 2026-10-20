import { LoadingLabel, SkeletonPanel, SkeletonTitle } from "@/components/Skeleton";

export default function Loading() {
  return (
    <div className="space-y-4">
      <LoadingLabel what="moves" />
      <SkeletonTitle title="Moves" />
      <SkeletonPanel rows={4} />
      <SkeletonPanel rows={3} />
      <SkeletonPanel rows={3} />
    </div>
  );
}
