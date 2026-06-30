"use client";

import { useEffect, useState } from "react";

const SHOW_AFTER_PX = 320;

export function ScrollToTop() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const onScroll = () => setVisible(window.scrollY > SHOW_AFTER_PX);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  if (!visible) return null;

  return (
    <button
      type="button"
      aria-label="回到顶部"
      className="glass fixed bottom-5 right-5 z-50 flex h-11 w-11 items-center justify-center rounded-full border border-white/10 text-[rgba(255,255,255,0.75)] transition-colors duration-200 ease-out hover:border-white/20 hover:bg-white/[0.06] focus:outline-none focus-visible:ring-1 focus-visible:ring-white/30"
      onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
    >
      <svg
        className="h-5 w-5"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        <path d="M12 19V5M5 12l7-7 7 7" />
      </svg>
    </button>
  );
}
