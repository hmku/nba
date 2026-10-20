import { Bone, LoadingLabel, SkeletonPanel } from "@/components/Skeleton";

export default function Loading() {
  return (
    <div className="space-y-3">
      <LoadingLabel what="players" />
      <h1 className="text-lg font-semibold tracking-tight">Players</h1>
      <Bone className="h-9 w-full rounded-lg" />
      <SkeletonPanel rows={14} head={false} />
    </div>
  );
}
