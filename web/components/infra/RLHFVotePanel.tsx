"use client";

import { useCallback, useEffect, useState } from "react";
import { ControlPlaneSection } from "./ControlPlaneSection";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type BtItem = {
  path: string;
  bt_score: number;
  wins: number;
  losses: number;
  comparisons: number;
  rank: number;
};

type RankingsData = {
  session_key: string | null;
  total_votes: number;
  items: BtItem[];
};

type PairData = {
  pair: [string, string] | null;
  total_votes: number;
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const API_BASE =
  typeof window !== "undefined" && window.location.port === "3000"
    ? "http://127.0.0.1:8080"
    : "";

function basename(p: string) {
  return p.split("/").pop() ?? p;
}

function imageUrl(base: string, path: string, maxSide: number) {
  return `${base.replace(/\/$/, "")}/image?path=${encodeURIComponent(path)}&max_side=${maxSide}`;
}

function scoreBar(score: number) {
  const pct = Math.round(score * 100);
  const color =
    pct >= 75 ? "bg-emerald-500" : pct >= 40 ? "bg-amber-500" : "bg-red-500";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-zinc-700">
        <div className={`h-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono text-xs text-zinc-300">{score.toFixed(3)}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function RLHFVotePanel({
  sessionKey,
  apiBase,
}: {
  sessionKey?: string | null;
  apiBase?: string;
}) {
  const base = apiBase ?? API_BASE;
  const skParam = sessionKey ? `?session_key=${encodeURIComponent(sessionKey)}` : "";

  const [pair, setPair] = useState<[string, string] | null>(null);
  const [totalVotes, setTotalVotes] = useState(0);
  const [rankings, setRankings] = useState<BtItem[]>([]);
  const [rankVotes, setRankVotes] = useState(0);
  const [voting, setVoting] = useState(false);
  const [lastChoice, setLastChoice] = useState<string | null>(null);

  const fetchPair = useCallback(async () => {
    try {
      const res = await fetch(`${base}/api/rlhf/pair${skParam}`);
      const d: PairData = await res.json();
      setPair(d.pair);
      setTotalVotes(d.total_votes);
    } catch {}
  }, [base, skParam]);

  const fetchRankings = useCallback(async () => {
    try {
      const res = await fetch(`${base}/api/rlhf/rankings${skParam}`);
      const d: RankingsData = await res.json();
      setRankings(d.items);
      setRankVotes(d.total_votes);
    } catch {}
  }, [base, skParam]);

  useEffect(() => {
    fetchPair();
    fetchRankings();
  }, [fetchPair, fetchRankings]);

  async function vote(winnerIdx: 0 | 1) {
    if (!pair || voting) return;
    setVoting(true);
    setLastChoice(pair[winnerIdx]);
    try {
      await fetch(`${base}/api/rlhf/vote`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          winner_path: pair[winnerIdx],
          loser_path: pair[1 - winnerIdx],
          session_key: sessionKey ?? null,
        }),
      });
      await fetchPair();
      await fetchRankings();
    } finally {
      setVoting(false);
    }
  }

  return (
    <ControlPlaneSection
      eyebrow="RLHF"
      title="Pairwise Preference Voting"
      subtitle={`Bradley-Terry reward model · ${rankVotes} vote${rankVotes !== 1 ? "s" : ""} collected · ${rankings.length} photos ranked`}
    >
      {/* Vote widget */}
      <div className="mb-5 rounded-xl border border-zinc-700/60 bg-zinc-900/60 p-4">
        <p className="mb-3 text-xs font-medium uppercase tracking-widest text-zinc-500">
          Which photo is better?
        </p>
        {pair ? (
          <div className="grid grid-cols-2 gap-3">
            {([0, 1] as const).map((idx) => {
              const name = basename(pair[idx]);
              const isWinner = lastChoice === pair[idx];
              return (
                <button
                  key={idx}
                  disabled={voting}
                  onClick={() => vote(idx)}
                  className={[
                    "group relative flex flex-col overflow-hidden rounded-lg border text-center transition-all",
                    "border-zinc-700 bg-zinc-800/60 hover:border-indigo-500 hover:bg-zinc-800",
                    "disabled:opacity-50 disabled:cursor-not-allowed",
                    isWinner ? "border-emerald-500 bg-emerald-900/20" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                >
                  <div className="relative aspect-[3/2] w-full bg-zinc-950">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={imageUrl(base, pair[idx], 640)}
                      alt={name}
                      loading="lazy"
                      className="h-full w-full object-cover"
                      onError={(e) => {
                        const el = e.currentTarget;
                        el.style.display = "none";
                        const fb = el.nextElementSibling as HTMLElement | null;
                        if (fb) fb.style.display = "flex";
                      }}
                    />
                    <div
                      className="absolute inset-0 hidden items-center justify-center text-3xl"
                      aria-hidden
                    >
                      {idx === 0 ? "🅰️" : "🅱️"}
                    </div>
                    <span className="absolute left-2 top-2 rounded bg-black/55 px-1.5 py-0.5 text-sm font-bold text-white">
                      {idx === 0 ? "A" : "B"}
                    </span>
                    {isWinner && (
                      <span className="absolute right-2 top-2 rounded bg-emerald-600/80 px-1.5 py-0.5 text-[10px] font-bold text-white">
                        ✓ chosen
                      </span>
                    )}
                  </div>
                  <span className="max-w-full truncate px-2 py-1.5 font-mono text-[11px] text-zinc-300">
                    {name}
                  </span>
                </button>
              );
            })}
          </div>
        ) : (
          <p className="py-4 text-center text-sm text-zinc-500">
            No images to compare yet. Trigger a pipeline run to populate candidates.
          </p>
        )}
        <p className="mt-2 text-right font-mono text-[10px] text-zinc-600">
          {totalVotes} total votes
        </p>
      </div>

      {/* Bradley-Terry leaderboard */}
      {rankings.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-zinc-700/50 text-[10px] uppercase tracking-widest text-zinc-500">
                <th className="pb-2 pr-3">Rank</th>
                <th className="pb-2 pr-4">Image</th>
                <th className="pb-2 pr-4">BT Score</th>
                <th className="pb-2 pr-3">W / L</th>
                <th className="pb-2">Comparisons</th>
              </tr>
            </thead>
            <tbody>
              {rankings.slice(0, 12).map((item) => (
                <tr key={item.path} className="border-b border-zinc-800/60 hover:bg-zinc-800/30">
                  <td className="py-1.5 pr-3 font-mono text-zinc-400">#{item.rank}</td>
                  <td className="py-1.5 pr-4 font-mono text-zinc-300">
                    <span className="flex items-center gap-2">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={imageUrl(base, item.path, 96)}
                        alt={basename(item.path)}
                        loading="lazy"
                        className="h-8 w-12 shrink-0 rounded border border-zinc-700 object-cover"
                        onError={(e) => {
                          e.currentTarget.style.visibility = "hidden";
                        }}
                      />
                      <span className="inline-block max-w-[160px] truncate" title={item.path}>
                        {basename(item.path)}
                      </span>
                    </span>
                  </td>
                  <td className="py-1.5 pr-4">{scoreBar(item.bt_score)}</td>
                  <td className="py-1.5 pr-3">
                    <span className="text-emerald-400">{item.wins}W</span>
                    <span className="text-zinc-600"> / </span>
                    <span className="text-red-400">{item.losses}L</span>
                  </td>
                  <td className="py-1.5 font-mono text-zinc-500">{item.comparisons}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {rankings.length > 12 && (
            <p className="mt-2 text-right font-mono text-[10px] text-zinc-600">
              +{rankings.length - 12} more images not shown
            </p>
          )}
        </div>
      )}
    </ControlPlaneSection>
  );
}
