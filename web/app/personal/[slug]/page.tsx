import { notFound } from "next/navigation";
import { PersonalComingSoon } from "@/components/personal/PersonalComingSoon";
import { getPersonalFeature } from "@/lib/personalFeatures";

type Props = {
  params: { slug: string };
};

export default function PersonalPlaceholderFeaturePage({ params }: Props) {
  const feature = getPersonalFeature(params.slug);
  if (!feature || feature.available) notFound();
  return <PersonalComingSoon feature={feature} />;
}
