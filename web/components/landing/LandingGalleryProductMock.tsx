"use client";

import { useState } from "react";
import {
  LANDING_GALLERY_FEATURES,
  LANDING_GALLERY_MOCK_FRAMES,
  LANDING_GALLERY_STYLE_PRESETS,
} from "./landingConfig";

type Props = {
  buildImageUrl: (path: string) => string;
  activeFeature: string;
  onFeatureHover: (id: string) => void;
};

function scoreText(n: number): string {
  return n.toFixed(1);
}

/**
 * Product mock uses fixed showcase cover paths + matching overlays.
 * Do not index-align against the landing gallery API list (session order drifts).
 */
export function LandingGalleryProductMock({ buildImageUrl, activeFeature, onFeatureHover }: Props) {
  const tiles = LANDING_GALLERY_MOCK_FRAMES;
  const [focusPath, setFocusPath] = useState(tiles[0].path);
  const focus = tiles.find((f) => f.path === focusPath) ?? tiles[0];

  return (
    <div className="landing-gallery-product">
      <div className="landing-gallery-product-features" role="tablist" aria-label="Gallery capabilities">
        {LANDING_GALLERY_FEATURES.map((f) => (
          <button
            key={f.id}
            type="button"
            role="tab"
            aria-selected={activeFeature === f.id}
            className={`landing-gallery-product-feature ${activeFeature === f.id ? "is-active" : ""}`}
            onMouseEnter={() => onFeatureHover(f.id)}
            onFocus={() => onFeatureHover(f.id)}
          >
            <span className="landing-gallery-product-feature-label">{f.label}</span>
            <span className="landing-gallery-product-feature-desc">{f.description}</span>
          </button>
        ))}
      </div>

      <div className="landing-gallery-product-frame">
        <div className="landing-gallery-product-chrome">
          <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-white/35">Luma Lab</span>
          <span className="font-mono text-[9px] tabular-nums text-white/28">248 photos · sorted</span>
        </div>

        <div className="landing-gallery-product-body">
          <div
            className={`landing-gallery-product-masonry ${activeFeature === "select" ? "highlight-select" : ""} ${activeFeature === "score" ? "highlight-score" : ""}`}
          >
            {tiles.map((frame, i) => (
              <article
                key={frame.path}
                className={`landing-gallery-product-tile ${frame.selected ? "is-selected" : ""} ${i === 1 ? "tile-tall" : ""} ${focus.path === frame.path ? "is-focus" : ""}`}
                onMouseEnter={() => setFocusPath(frame.path)}
                onFocus={() => setFocusPath(frame.path)}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={buildImageUrl(frame.path)}
                  alt=""
                  className="landing-gallery-product-tile-img"
                  loading="lazy"
                />
                <div className="landing-gallery-product-tile-cap">
                  <span className="truncate text-[10px] text-white/75">{frame.file}</span>
                  <span className="tabular-nums text-[10px] text-white/50">{scoreText(frame.score)}</span>
                </div>
                <span className={`landing-gallery-product-tile-pick ${frame.selected ? "is-on" : ""}`}>
                  {frame.selected ? "已选" : "选择"}
                </span>
              </article>
            ))}
          </div>

          <aside
            className={`landing-gallery-product-panel ${activeFeature === "tags" || activeFeature === "score" ? "is-lit" : ""}`}
          >
            <p className="font-mono text-[10px] text-white/45">{focus.file}</p>
            <div className={`landing-gallery-product-score ${activeFeature === "score" ? "is-lit" : ""}`}>
              <p className="landing-gallery-product-score-main tabular-nums">{scoreText(focus.score)}</p>
              <p className="mt-1 font-mono text-[9px] tabular-nums text-white/32">
                E {scoreText(focus.energy)} · T {scoreText(focus.technical)} · C {scoreText(focus.composition)}
              </p>
            </div>
            <ul className={`landing-gallery-product-tags ${activeFeature === "tags" ? "is-lit" : ""}`}>
              {focus.tags.map((t) => (
                <li key={t}>#{t}</li>
              ))}
            </ul>
            <p className="landing-gallery-product-ai">{focus.aiLine}</p>

            <div className={`landing-gallery-product-styles ${activeFeature === "style" ? "is-lit" : ""}`}>
              <p className="mb-2 font-mono text-[9px] uppercase tracking-[0.16em] text-white/28">风格预览</p>
              <div className="landing-gallery-product-style-rail">
                {LANDING_GALLERY_STYLE_PRESETS.map((s) => (
                  <span
                    key={s.id}
                    className={`landing-gallery-product-style-chip ${"active" in s && s.active ? "is-active" : ""}`}
                  >
                    {s.label}
                  </span>
                ))}
              </div>
            </div>
          </aside>
        </div>

        <div className={`landing-gallery-product-export ${activeFeature === "export" ? "is-lit" : ""}`}>
          <span className="tabular-nums text-[11px] text-white/55">
            已选 <span className="text-white/80">2</span> 张
          </span>
          <span className="text-white/20">·</span>
          <span className="text-[11px] text-white/45">{focus.exportStyle ?? "默认胶片"} · 批量导出</span>
          <button type="button" className="landing-gallery-product-export-btn">
            Export
          </button>
        </div>
      </div>
    </div>
  );
}
