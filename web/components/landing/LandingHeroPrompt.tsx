"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { LANDING_HERO, LANDING_HERO_PROMPTS } from "@/lib/productIa";

const ROTATE_MS = 3200;

export function LandingHeroPrompt() {
  const router = useRouter();
  const { promptIdle, promptSubmitHref, promptCtas } = LANDING_HERO;
  const [index, setIndex] = useState(0);
  const [fade, setFade] = useState(true);
  const [value, setValue] = useState("");
  const [focused, setFocused] = useState(false);

  const rotating = !focused && value.trim().length === 0;
  const activePrompt = LANDING_HERO_PROMPTS[index % LANDING_HERO_PROMPTS.length];

  useEffect(() => {
    if (!rotating) return;

    let fadeTimer = 0;
    const id = window.setInterval(() => {
      setFade(false);
      fadeTimer = window.setTimeout(() => {
        setIndex((i) => (i + 1) % LANDING_HERO_PROMPTS.length);
        setFade(true);
      }, 220);
    }, ROTATE_MS);

    return () => {
      window.clearInterval(id);
      window.clearTimeout(fadeTimer);
    };
  }, [rotating]);

  function goWithPrompt(raw: string) {
    const q = raw.trim() || activePrompt;
    const url = `${promptSubmitHref}?q=${encodeURIComponent(q)}`;
    router.push(url);
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    goWithPrompt(value);
  }

  return (
    <div className="landing-hero-prompt">
      <form className="landing-hero-prompt-box" onSubmit={onSubmit}>
        <div className="landing-hero-prompt-field">
          {rotating ? (
            <span
              className={`landing-hero-prompt-rotator ${fade ? "is-in" : "is-out"}`}
              aria-live="polite"
            >
              {activePrompt}
            </span>
          ) : null}
          <textarea
            rows={3}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                (e.currentTarget.form as HTMLFormElement | null)?.requestSubmit();
              }
            }}
            placeholder={focused || value ? promptIdle : " "}
            aria-label="系统能做什么"
            className="landing-hero-prompt-input"
            autoComplete="off"
          />
        </div>
        <button type="submit" className="landing-hero-prompt-send" aria-label="去 Gallery 试试">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
            <path
              d="M8 12.5V3.5M8 3.5L3.5 8M8 3.5L12.5 8"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </form>

      <div className="landing-hero-prompt-pills">
        {promptCtas.map((cta) => (
          <Link key={cta.href} href={cta.href} className="landing-hero-prompt-pill">
            {cta.label}
          </Link>
        ))}
      </div>
    </div>
  );
}
