import { LoadingLabel, SkeletonPanel } from "@/components/Skeleton";

export default function Loading() {
  return (
    <div className="space-y-3">
      <LoadingLabel what="history" />
      <h1 className="text-lg font-semibold tracking-tight">History</h1>
      <div className="grid gap-3 md:grid-cols-2">
        <SkeletonPanel rows={5} />
        <SkeletonPanel rows={5} />
      </div>
      <SkeletonPanel rows={8} />
    </div>
  );
}
