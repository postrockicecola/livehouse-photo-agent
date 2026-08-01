import { ProductGatePortal } from "@/components/landing/ProductGatePortal";

/** Product mode chooser — no silent redirect; remembered mode is a one-click resume. */
export default function GatePage() {
  return (
    <div className="min-h-screen bg-[var(--luma-bg)] text-[var(--luma-text)]">
      <ProductGatePortal />
    </div>
  );
}
