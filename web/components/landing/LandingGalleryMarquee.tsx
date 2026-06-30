"use client";

import { useCallback, useEffect, useRef } from "react";

type GalleryImage = {
  path: string;
};

type Props = {
  images: GalleryImage[];
  buildImageUrl: (path: string) => string;
};

const AUTO_SCROLL_PX_PER_FRAME = 0.3;
const RESUME_AUTO_MS = 2800;

export function LandingGalleryMarquee({ images, buildImageUrl }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const loopWidthRef = useRef(0);
  const autoPausedRef = useRef(false);
  const isAutoScrollingRef = useRef(false);
  const resumeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dragRef = useRef({
    active: false,
    pointerId: -1,
    startX: 0,
    startScrollLeft: 0,
  });

  const normalizeScroll = useCallback(() => {
    const el = scrollRef.current;
    const half = loopWidthRef.current;
    if (!el || half <= 0) return;
    if (el.scrollLeft >= half) el.scrollLeft -= half;
    if (el.scrollLeft < 0) el.scrollLeft += half;
  }, []);

  const pauseAuto = useCallback(() => {
    autoPausedRef.current = true;
    if (resumeTimerRef.current) clearTimeout(resumeTimerRef.current);
  }, []);

  const scheduleResume = useCallback((delay = RESUME_AUTO_MS) => {
    if (resumeTimerRef.current) clearTimeout(resumeTimerRef.current);
    resumeTimerRef.current = setTimeout(() => {
      autoPausedRef.current = false;
    }, delay);
  }, []);

  useEffect(() => {
    const track = trackRef.current;
    if (!track) return;

    const update = () => {
      loopWidthRef.current = track.scrollWidth / 2;
      normalizeScroll();
    };
    update();

    const ro = new ResizeObserver(update);
    ro.observe(track);
    return () => ro.disconnect();
  }, [images, normalizeScroll]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || images.length === 0) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (reduced.matches) return;

    let raf = 0;
    const tick = () => {
      if (!autoPausedRef.current && loopWidthRef.current > 0) {
        isAutoScrollingRef.current = true;
        el.scrollLeft += AUTO_SCROLL_PX_PER_FRAME;
        normalizeScroll();
        isAutoScrollingRef.current = false;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    return () => cancelAnimationFrame(raf);
  }, [images, normalizeScroll]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const onWheel = (e: WheelEvent) => {
      if (isAutoScrollingRef.current) return;
      const horizontal = Math.abs(e.deltaX) > Math.abs(e.deltaY) || e.shiftKey;
      if (!horizontal) return;
      pauseAuto();
      scheduleResume();
    };

    el.addEventListener("wheel", onWheel, { passive: true });
    return () => el.removeEventListener("wheel", onWheel);
  }, [pauseAuto, scheduleResume]);

  useEffect(() => {
    return () => {
      if (resumeTimerRef.current) clearTimeout(resumeTimerRef.current);
    };
  }, []);

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = scrollRef.current;
    if (!el || e.button !== 0) return;
    pauseAuto();
    if (e.pointerType === "touch") return;
    dragRef.current = {
      active: true,
      pointerId: e.pointerId,
      startX: e.clientX,
      startScrollLeft: el.scrollLeft,
    };
    el.setPointerCapture(e.pointerId);
    el.classList.add("landing-gallery-marquee-wrap--dragging");
  };

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = scrollRef.current;
    const drag = dragRef.current;
    if (!el || !drag.active || e.pointerId !== drag.pointerId) return;
    const dx = drag.startX - e.clientX;
    el.scrollLeft = drag.startScrollLeft + dx;
    normalizeScroll();
  };

  const endDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = scrollRef.current;
    const drag = dragRef.current;
    if (!el || !drag.active || e.pointerId !== drag.pointerId) return;
    drag.active = false;
    try {
      el.releasePointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
    el.classList.remove("landing-gallery-marquee-wrap--dragging");
    normalizeScroll();
    scheduleResume();
  };

  const onTouchStart = () => pauseAuto();
  const onTouchEnd = () => scheduleResume();

  const duplicated = [...images, ...images];

  return (
    <div
      ref={scrollRef}
      className="landing-gallery-marquee-wrap landing-gallery-marquee-wrap--interactive"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
      onTouchCancel={onTouchEnd}
      role="region"
      aria-label="Gallery preview strip, drag or swipe to browse"
    >
      <div ref={trackRef} className="landing-gallery-marquee-track landing-gallery-marquee-track--scroll">
        {duplicated.map((img, i) => (
          <figure key={`${img.path}-${i}`} className="landing-gallery-slide">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={buildImageUrl(img.path)}
              alt=""
              loading={i < images.length ? "eager" : "lazy"}
              decoding="async"
              draggable={false}
              className="landing-gallery-img"
            />
          </figure>
        ))}
      </div>
    </div>
  );
}
