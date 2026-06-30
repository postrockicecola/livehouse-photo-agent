"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { LANDING_PRODUCT_MATRIX, type ProductMatrixItem } from "@/lib/productIa";

function ProductVisual({ id }: { id: ProductMatrixItem["id"] }) {
  if (id === "studio") {
    return (
      <div className="landing-matrix-visual landing-matrix-visual--studio" aria-hidden>
        <span />
        <span />
        <span />
      </div>
    );
  }
  if (id === "gallery") {
    return (
      <div className="landing-matrix-visual landing-matrix-visual--gallery" aria-hidden>
        <span />
        <span />
        <span />
        <span />
      </div>
    );
  }
  if (id === "brain") {
    return (
      <div className="landing-matrix-visual landing-matrix-visual--brain" aria-hidden>
        <span />
        <span />
        <span />
        <span />
        <span />
      </div>
    );
  }
  return (
    <div className="landing-matrix-visual landing-matrix-visual--infra" aria-hidden>
      <span />
      <span />
      <span />
      <span />
    </div>
  );
}

function ProductTile({
  product,
  index,
  visible,
}: {
  product: ProductMatrixItem;
  index: number;
  visible: boolean;
}) {
  const { id, name, role, description, href, showcaseHref, featured } = product;

  return (
    <article
      className={`landing-matrix-tile ${featured ? "landing-matrix-tile--featured" : ""} ${
        visible ? "landing-matrix-tile--visible" : ""
      }`}
      style={{ transitionDelay: `${index * 70}ms` }}
    >
      <Link href={href} className="landing-matrix-tile-link">
        <ProductVisual id={id} />
        <div className="landing-matrix-tile-body">
          <p className="landing-matrix-tile-role">{role}</p>
          <h3 className="landing-matrix-tile-name">{name}</h3>
          <p className="landing-matrix-tile-desc">{description}</p>
        </div>
        <span className="landing-matrix-tile-cta">Open →</span>
      </Link>
      {showcaseHref ? (
        <Link href={showcaseHref} className="landing-matrix-tile-showcase">
          On this page
        </Link>
      ) : null}
    </article>
  );
}

export function LandingProductMatrixSection() {
  const sectionRef = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  const { id, eyebrow, title, subtitle, products } = LANDING_PRODUCT_MATRIX;

  useEffect(() => {
    const node = sectionRef.current;
    if (!node) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { threshold: 0.12 },
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <section
      ref={sectionRef}
      id={id}
      className={`landing-matrix scroll-mt-24 ${visible ? "landing-matrix--visible" : ""}`}
      aria-labelledby="landing-matrix-title"
    >
      <div className="relative mx-auto w-full max-w-[104rem] px-5 py-24 sm:px-8 sm:py-32 lg:px-12">
        <header className="mx-auto max-w-3xl text-center">
          <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-white/32">{eyebrow}</p>
          <h2
            id="landing-matrix-title"
            className="landing-matrix-headline mt-4 text-[clamp(2rem,4.5vw,3.25rem)] font-light leading-[1.08] tracking-[-0.03em] text-white/[0.92]"
          >
            {title}
          </h2>
          <p className="mt-5 text-sm leading-relaxed text-white/38 sm:text-base">{subtitle}</p>
        </header>

        <div className="landing-matrix-grid mt-14 sm:mt-16">
          {products.map((product, index) => (
            <ProductTile key={product.id} product={product} index={index} visible={visible} />
          ))}
        </div>
      </div>
    </section>
  );
}
