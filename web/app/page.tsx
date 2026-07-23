import { LandingPage } from "@/components/landing/LandingPage";
import { LANDING_HERO } from "@/lib/productIa";

export default function HomePage() {
  return (
    <>
      <link rel="preload" as="image" href={LANDING_HERO.backgroundSrc} fetchPriority="high" />
      <LandingPage />
    </>
  );
}
