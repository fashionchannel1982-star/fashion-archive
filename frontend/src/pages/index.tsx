/**
 * Fashion Archive — Search Page
 * Apple TV aesthetic × Google search simplicity
 */

import { useState, useRef, useCallback, useEffect } from "react";
import type Hls from "hls.js";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ─────────────────────────────────────────
// CONSTANTS
// ─────────────────────────────────────────

const HERO_QUERY = "sheer black evening looks";

// Locked demo chips — each verified to return ≥1 strong result (conf ≥75).
// Do NOT add chips without probing first.
const CURATED_QUERIES = [
  "sheer black evening looks",
  "structured shoulders, sharp tailoring",
  "monochrome white, head to toe",
  "maximalist print colour runway",
  "Chanel tweed and tailoring",
  "Dior structured tailoring",
  "red dress",
];

// ─────────────────────────────────────────
// INSTRUMENTATION
// ─────────────────────────────────────────

const log = (event: string, data: Record<string, unknown>) => {
  const entry = { ts: new Date().toISOString(), event, ...data };
  console.log("[FA]", JSON.stringify(entry));
  try {
    const existing = JSON.parse(localStorage.getItem("fa_events") || "[]");
    existing.push(entry);
    if (existing.length > 500) existing.splice(0, existing.length - 500);
    localStorage.setItem("fa_events", JSON.stringify(existing));
  } catch {}
};

// ─────────────────────────────────────────
// TYPES
// ─────────────────────────────────────────

interface SearchResult {
  moment_id: string;
  show_id: string;
  show_key?: string | null;
  brand: string;
  season: string;
  season_type?: string | null;
  year: number;
  timestamp_start: number;
  timestamp_end: number;
  description: string;
  thumbnail_url?: string;
  confidence: number;
  score_raw: number;
  match_type?: string | null;
  creative_director?: string | null;
  show_date?: string | null;
  source?: string | null;
  enriched?: {
    garments?: string[];
    colours?: string[];
    silhouette?: string;
  } | null;
}

interface SearchResponse {
  query: string;
  results: SearchResult[];
  total: number;
  processing_time_ms: number;
  synthesis?: string | null;
}

interface ShowItem {
  id: string;
  brand: string;
  season: string;
  year: number;
  moment_count: number;
  status: string;
  summary?: string;
}

// ─────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────

// CONFIDENCE_MODE controls how match strength is presented:
//   "bucket"  — default: ● Exact / ● Strong / ● Relevant  (no raw number)
//   "number"  — raw score: 9/10 · Exact  (previous behaviour)
//   "hidden"  — dot only (no text)
// Change this constant to switch the display; no other code changes needed.
const CONFIDENCE_MODE: "bucket" | "number" | "hidden" = "bucket";

function confidenceLabel(c: number): string {
  if (c >= 90) return "Exact";
  if (c >= 75) return "Strong";
  return "Relevant";
}

function confidenceColor(c: number): string {
  if (c >= 90) return "#4ADE80";
  if (c >= 75) return "#FACC15";
  return "#94A3B8";
}

function formatTimestamp(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function formatShowDate(iso: string | null | undefined): string {
  if (!iso) return "";
  try {
    // Parse parts to avoid UTC-midnight timezone shift (Oct 21 → Oct 20 in UTC-offset zones)
    const parts = iso.split("-").map(Number);
    const d = new Date(parts[0], (parts[1] || 1) - 1, 1);
    return d.toLocaleDateString("en-US", { month: "long", year: "numeric" });
  } catch {
    return "";
  }
}

function diverseShows(shows: ShowItem[]): ShowItem[] {
  const groupMap: Record<string, ShowItem[]> = {};
  for (const s of shows) {
    if (!groupMap[s.brand]) groupMap[s.brand] = [];
    groupMap[s.brand].push(s);
  }
  // Within each brand, most recent year first
  for (const key of Object.keys(groupMap)) {
    groupMap[key].sort((a: ShowItem, b: ShowItem) => b.year - a.year);
  }
  // Round-robin interleave: brands with fewer shows go first in each cycle
  const cols: ShowItem[][] = Object.values(groupMap).sort(
    (a: ShowItem[], b: ShowItem[]) => a.length - b.length
  );
  const out: ShowItem[] = [];
  let added = true;
  while (added) {
    added = false;
    for (const col of cols) {
      const s = col.shift();
      if (s) { out.push(s); added = true; }
    }
  }
  return out;
}

function formatSource(source: string | null | undefined): string {
  if (!source) return "";
  if (source === "fc_master") return "FC Master";
  if (source === "youtube_mvp") return "YouTube";
  return source;
}

// ─────────────────────────────────────────
// HLS LOAD (shared logic)
// ─────────────────────────────────────────

async function loadHls(
  momentId: string,
  video: HTMLVideoElement,
  opts: { muted?: boolean; onError?: (e: string) => void } = {}
): Promise<(() => void) | null> {
  const res = await fetch(`${API_URL}/api/moments/${momentId}/play`);
  if (!res.ok) throw new Error(`${res.status}`);
  const data = await res.json();

  const MAX = 120;
  const clipStart = data.timestamp_start;
  const clipEnd = Math.min(
    data.timestamp_end > clipStart ? data.timestamp_end : clipStart + MAX,
    clipStart + MAX
  );

  video.muted = opts.muted ?? false;

  const onTimeUpdate = () => {
    if (video.currentTime >= clipEnd) {
      video.currentTime = clipStart;
    }
  };
  video.addEventListener("timeupdate", onTimeUpdate);

  let hls: Hls | null = null;

  const HlsLib = (await import("hls.js")).default;
  if (HlsLib.isSupported()) {
    hls = new HlsLib({ startPosition: clipStart });
    hls.loadSource(data.hls_url);
    hls.attachMedia(video);
    hls.on(HlsLib.Events.MANIFEST_PARSED, () => {
      video.currentTime = clipStart;
      video.play().catch(() => {});
    });
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = data.hls_url;
    video.currentTime = clipStart;
    video.play().catch(() => {});
  } else {
    opts.onError?.("HLS not supported");
  }

  return () => {
    video.removeEventListener("timeupdate", onTimeUpdate);
    hls?.destroy();
  };
}

// ─────────────────────────────────────────
// HERO MOMENT
// ─────────────────────────────────────────

function HeroMoment({
  result,
  onPlay,
}: {
  result: SearchResult;
  onPlay: (r: SearchResult) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [loaded, setLoaded] = useState(false);
  const [fallback, setFallback] = useState(false);
  const cleanupRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (!videoRef.current) return;
    const video = videoRef.current;
    let cancelled = false;

    loadHls(result.moment_id, video, { muted: true, onError: () => setFallback(true) })
      .then((cleanup) => {
        if (!cancelled) {
          cleanupRef.current = cleanup;
          setLoaded(true);
          log("hero_play", { moment_id: result.moment_id, brand: result.brand });
        }
      })
      .catch(() => { if (!cancelled) setFallback(true); });

    return () => {
      cancelled = true;
      cleanupRef.current?.();
    };
  }, [result.moment_id]);

  return (
    <div
      onClick={() => { onPlay(result); log("hero_click", { moment_id: result.moment_id }); }}
      style={{
        position: "relative",
        width: "100%",
        maxWidth: 960,
        margin: "0 auto 40px",
        borderRadius: 10,
        overflow: "hidden",
        cursor: "pointer",
        aspectRatio: "16/9",
        background: "#0D0D0D",
        border: "1px solid rgba(255,255,255,0.07)",
      }}
    >
      {/* Video or thumbnail */}
      {!fallback ? (
        <video
          ref={videoRef}
          muted
          autoPlay
          loop
          playsInline
          style={{
            width: "100%", height: "100%", objectFit: "cover", display: "block",
            opacity: loaded ? 1 : 0, transition: "opacity 0.6s ease",
          }}
        />
      ) : null}

      {/* Thumbnail fallback (always rendered behind video) */}
      {result.thumbnail_url && (
        <img
          src={result.thumbnail_url}
          alt={result.description}
          style={{
            position: "absolute", inset: 0,
            width: "100%", height: "100%", objectFit: "cover",
            opacity: loaded && !fallback ? 0 : 1,
            transition: "opacity 0.6s ease",
          }}
        />
      )}

      {/* Gradient overlay */}
      <div style={{
        position: "absolute", inset: 0,
        background: "linear-gradient(to top, rgba(0,0,0,0.72) 0%, rgba(0,0,0,0.1) 50%, transparent 100%)",
      }} />

      {/* Play affordance */}
      <div style={{
        position: "absolute", inset: 0,
        display: "flex", alignItems: "center", justifyContent: "center",
        opacity: 0, transition: "opacity 0.2s",
      }}
        className="hero-play-overlay"
      >
        <div style={{
          width: 56, height: 56, borderRadius: "50%",
          background: "rgba(255,255,255,0.18)", backdropFilter: "blur(8px)",
          border: "1px solid rgba(255,255,255,0.25)",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 18, color: "#F5F5F0", paddingLeft: 4,
        }}>▶</div>
      </div>

      {/* Provenance overlay */}
      <div style={{
        position: "absolute", bottom: 0, left: 0, right: 0,
        padding: "24px 28px 22px",
      }}>
        <div style={{
          fontFamily: "var(--font-display)",
          fontSize: 22, fontWeight: 300,
          color: "#F5F5F0", letterSpacing: "0.1em",
          marginBottom: 6,
          textShadow: "0 1px 8px rgba(0,0,0,0.6)",
        }}>
          {result.brand.toUpperCase()} · {result.season}
        </div>
        <div style={{
          fontFamily: "var(--font-body)", fontSize: 12, color: "rgba(245,245,240,0.75)",
          lineHeight: 1.5, maxWidth: 600,
          textShadow: "0 1px 6px rgba(0,0,0,0.6)",
        }}>
          {result.description}
        </div>
        <div style={{
          marginTop: 8,
          fontFamily: "var(--font-body)", fontSize: 10, color: "rgba(200,200,195,0.6)",
          letterSpacing: "0.08em",
        }}>
          Click to play
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────
// RESULT CARD
// ─────────────────────────────────────────

function ResultCard({
  result,
  bookmarks,
  pinned,
  highlighted,
  onBookmark,
  onPin,
  onExport,
  onPlay,
}: {
  result: SearchResult;
  bookmarks: Set<string>;
  pinned: Set<string>;
  highlighted: boolean;
  onBookmark: (r: SearchResult) => void;
  onPin: (r: SearchResult) => void;
  onExport: (momentId: string, brand: string, ts: number, confidence?: number) => void;
  onPlay: (momentId: string) => void;
}) {
  const isBookmarked = bookmarks.has(result.moment_id);
  const isPinned = pinned.has(result.moment_id);

  // Provenance parts with explicit hierarchy — brand is in the header pill, not here
  const dateStr = formatShowDate(result.show_date);
  const sourceStr = formatSource(result.source);

  return (
    <div
      style={{
        background: highlighted ? "rgba(237,232,220,0.04)" : "#141414",
        borderRadius: 8,
        overflow: "hidden",
        transition: "transform 0.2s ease, background 0.2s ease",
        border: highlighted ? "1px solid rgba(237,232,220,0.12)" : "1px solid transparent",
        cursor: "default",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "#1C1C1C")}
      onMouseLeave={(e) => (e.currentTarget.style.background = highlighted ? "rgba(237,232,220,0.04)" : "#141414")}
    >
      {/* Thumbnail */}
      <div
        onClick={() => { onPlay(result.moment_id); log("result_click", { moment_id: result.moment_id, brand: result.brand, query: result.description.slice(0, 30) }); }}
        style={{
          aspectRatio: "16/9", background: "#0F0F0F",
          position: "relative", overflow: "hidden", cursor: "pointer",
        }}
      >
        {result.thumbnail_url ? (
          <>
            <img
              src={result.thumbnail_url}
              alt={result.description}
              style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
            />
            <div style={{
              position: "absolute", inset: 0,
              display: "flex", alignItems: "center", justifyContent: "center",
            }}>
              <div style={{
                width: 44, height: 44, borderRadius: "50%",
                background: "rgba(255,255,255,0.15)", backdropFilter: "blur(6px)",
                border: "1px solid rgba(255,255,255,0.2)",
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 15, color: "#F5F5F0", paddingLeft: 3,
                opacity: 0, transition: "opacity 0.18s",
              }} className="play-btn">▶</div>
            </div>
          </>
        ) : (
          <div style={{
            width: "100%", height: "100%",
            display: "flex", alignItems: "center", justifyContent: "center",
            color: "#2A2A2A", fontFamily: "var(--font-display)", fontSize: 13, letterSpacing: "0.2em",
          }}>
            {result.brand.toUpperCase()}
          </div>
        )}
        {/* Timestamp pill */}
        <div style={{
          position: "absolute", bottom: 8, left: 8,
          background: "rgba(0,0,0,0.75)", backdropFilter: "blur(4px)",
          borderRadius: 4, padding: "2px 8px",
          fontFamily: "var(--font-body)", fontSize: 11, color: "#F5F5F0", letterSpacing: "0.05em",
        }}>
          {formatTimestamp(result.timestamp_start)}
        </div>
      </div>

      {/* Body */}
      <div style={{ padding: "14px 16px 16px" }}>
        {/* Header row */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
          <span style={{
            fontFamily: "var(--font-body)", fontSize: 10, fontWeight: 500,
            letterSpacing: "0.15em", color: "#EDE8DC",
            background: "rgba(237,232,220,0.08)", borderRadius: 3, padding: "2px 7px",
            textTransform: "uppercase",
          }}>
            {result.brand}
          </span>
          {/* Archive badge — archival seasons feel premium, not tagged */}
          {result.year && result.year < 2020 && (
            <span style={{
              fontFamily: "var(--font-body)", fontSize: 9, letterSpacing: "0.12em",
              color: "#8A8A85", border: "1px solid rgba(138,138,133,0.25)",
              borderRadius: 2, padding: "1px 5px", textTransform: "uppercase",
            }}>
              {result.year} · Archive
            </span>
          )}
          <div style={{ flex: 1 }} />
          {/* Confidence badge — mode controlled by CONFIDENCE_MODE constant */}
          <span style={{
            fontFamily: "var(--font-body)", fontSize: 10, fontWeight: 500,
            color: confidenceColor(result.confidence), letterSpacing: "0.05em",
            display: "flex", alignItems: "center", gap: 4,
          }}>
            <span style={{
              width: 5, height: 5, borderRadius: "50%",
              background: confidenceColor(result.confidence), display: "inline-block",
            }} />
            {CONFIDENCE_MODE === "bucket" && confidenceLabel(result.confidence)}
            {CONFIDENCE_MODE === "number" && `${Math.round(result.confidence / 10)}/10 · ${confidenceLabel(result.confidence)}`}
            {/* hidden: dot only — no text */}
          </span>
        </div>

        {/* Description */}
        <p style={{
          fontFamily: "var(--font-display)", fontSize: 14, fontWeight: 300,
          color: "#F5F5F0", lineHeight: 1.6, margin: "0 0 10px",
          display: "-webkit-box", WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical", overflow: "hidden",
        }}>
          {result.description}
        </p>

        {/* Provenance — editorial credit line with explicit hierarchy */}
        <div style={{
          fontFamily: "var(--font-body)", fontSize: 10,
          marginBottom: 12, display: "flex", flexWrap: "wrap",
          alignItems: "baseline", gap: 0, lineHeight: 1.6,
        }}>
          {/* Season — lead element, slightly brighter */}
          {result.season && (
            <span style={{ color: "#A8A8A3", letterSpacing: "0.04em" }}>
              {result.season}
            </span>
          )}
          {/* Date — same register as season, muted */}
          {dateStr && (
            <span style={{ color: "#5A5A56", letterSpacing: "0.03em" }}>
              <span style={{ margin: "0 5px", color: "#2E2E2C" }}>·</span>
              {dateStr}
            </span>
          )}
          {/* Creative director — gold, the editorial credit */}
          {result.creative_director && (
            <span style={{ color: "#C8A97A", letterSpacing: "0.04em", fontWeight: 400 }}>
              <span style={{ margin: "0 5px", color: "#2E2E2C" }}>·</span>
              {result.creative_director}
            </span>
          )}
          {/* Source — most subtle, rightmost */}
          {sourceStr && (
            <span style={{ color: "#3E3E3C", letterSpacing: "0.04em" }}>
              <span style={{ margin: "0 5px", color: "#2A2A28" }}>·</span>
              {sourceStr}
            </span>
          )}
        </div>

        {/* Actions */}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            onClick={() => onBookmark(result)}
            style={{
              background: isBookmarked ? "rgba(237,232,220,0.12)" : "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: 4, padding: "5px 10px", cursor: "pointer",
              fontFamily: "var(--font-body)", fontSize: 11,
              color: isBookmarked ? "#EDE8DC" : "#8A8A85",
              transition: "all 0.15s", display: "flex", alignItems: "center", gap: 5,
            }}
          >
            {isBookmarked ? "✦ Saved" : "✦ Save"}
          </button>

          <button
            onClick={() => onPin(result)}
            style={{
              background: isPinned ? "rgba(74,222,128,0.12)" : "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: 4, padding: "5px 10px", cursor: "pointer",
              fontFamily: "var(--font-body)", fontSize: 11,
              color: isPinned ? "#4ADE80" : "#8A8A85",
              transition: "all 0.15s", display: "flex", alignItems: "center", gap: 5,
            }}
          >
            {isPinned ? "⊞ Collected" : "⊞ Collect"}
          </button>

          <button
            onClick={() => onExport(result.moment_id, result.brand, result.timestamp_start, result.confidence)}
            style={{
              background: "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: 4, padding: "5px 10px", cursor: "pointer",
              fontFamily: "var(--font-body)", fontSize: 11, color: "#8A8A85",
              transition: "all 0.15s", display: "flex", alignItems: "center", gap: 5,
            }}
            onMouseEnter={(e) => (e.currentTarget.style.color = "#F5F5F0")}
            onMouseLeave={(e) => (e.currentTarget.style.color = "#8A8A85")}
          >
            ↓ Export
          </button>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────
// VIDEO MODAL
// ─────────────────────────────────────────

const MAX_CLIP_DURATION = 120;

function VideoModal({
  momentId,
  brand,
  season,
  onClose,
}: {
  momentId: string;
  brand: string;
  season: string;
  onClose: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const hlsRef = useRef<Hls | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [clipDuration, setClipDuration] = useState(0);
  const startRef = useRef(0);
  const endRef = useRef(0);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const res = await fetch(`${API_URL}/api/moments/${momentId}/play`);
        if (!res.ok) throw new Error(`${res.status}`);
        const data = await res.json();
        if (cancelled || !videoRef.current) return;
        if (!data.hls_url) throw new Error("no hls_url");

        const clipStart = data.timestamp_start;
        const clipEnd = Math.min(
          data.timestamp_end > clipStart ? data.timestamp_end : clipStart + MAX_CLIP_DURATION,
          clipStart + MAX_CLIP_DURATION
        );
        startRef.current = clipStart;
        endRef.current = clipEnd;
        setClipDuration(clipEnd - clipStart);

        const video = videoRef.current;
        const onTimeUpdate = () => {
          if (video.currentTime >= endRef.current) {
            video.pause();
            video.currentTime = endRef.current;
          }
          const elapsed = Math.max(0, video.currentTime - startRef.current);
          setProgress(elapsed / (endRef.current - startRef.current));
        };
        video.addEventListener("timeupdate", onTimeUpdate);

        const HlsLib = (await import("hls.js")).default;
        if (HlsLib.isSupported()) {
          const hls = new HlsLib({ startPosition: clipStart });
          hlsRef.current = hls;
          hls.loadSource(data.hls_url);
          hls.attachMedia(video);
          hls.on(HlsLib.Events.MANIFEST_PARSED, () => {
            video.currentTime = clipStart;
            video.play().catch(() => {});
          });
        } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
          video.src = data.hls_url;
          video.currentTime = clipStart;
          video.play().catch(() => {});
        } else {
          setError("HLS playback not supported in this browser.");
        }

        return () => video.removeEventListener("timeupdate", onTimeUpdate);
      } catch {
        if (!cancelled) setError("Could not load video stream.");
      }
    }

    load();
    return () => {
      cancelled = true;
      hlsRef.current?.destroy();
      hlsRef.current = null;
    };
  }, [momentId]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const elapsed = Math.round(progress * clipDuration);
  const remaining = Math.round(clipDuration - elapsed);

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0,
        background: "rgba(0,0,0,0.88)", backdropFilter: "blur(8px)",
        zIndex: 200, display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "100%", maxWidth: 960,
          background: "#111", borderRadius: 10, overflow: "hidden",
          border: "1px solid rgba(255,255,255,0.08)",
        }}
      >
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "14px 20px", borderBottom: "1px solid rgba(255,255,255,0.06)",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{ fontFamily: "var(--font-display)", fontSize: 14, letterSpacing: "0.12em", color: "#EDE8DC" }}>
              {brand.toUpperCase()} · {season}
            </span>
            {clipDuration > 0 && (
              <span style={{ fontFamily: "var(--font-body)", fontSize: 11, color: "#8A8A85", letterSpacing: "0.05em" }}>
                {formatTimestamp(startRef.current)} — {formatTimestamp(endRef.current)}
                {clipDuration >= MAX_CLIP_DURATION && <span style={{ color: "#555", marginLeft: 6 }}>· clipped to 2 min</span>}
              </span>
            )}
          </div>
          <button onClick={onClose} style={{ background: "none", border: "none", color: "#8A8A85", cursor: "pointer", fontSize: 20, lineHeight: 1, padding: "0 4px" }}>×</button>
        </div>

        <div style={{ position: "relative", aspectRatio: "16/9", background: "#000" }}>
          {error ? (
            <div style={{
              position: "absolute", inset: 0, display: "flex", flexDirection: "column",
              alignItems: "center", justifyContent: "center", gap: 8,
            }}>
              <span style={{ fontFamily: "var(--font-display)", fontSize: 16, letterSpacing: "0.1em", color: "#3A3A3A" }}>Clip unavailable</span>
              <span style={{ fontFamily: "var(--font-body)", fontSize: 11, color: "#2A2A2A", letterSpacing: "0.04em" }}>The stream could not be loaded</span>
            </div>
          ) : (
            <video ref={videoRef} controls style={{ width: "100%", height: "100%", display: "block" }} />
          )}
        </div>

        {clipDuration > 0 && (
          <div style={{ padding: "10px 20px 14px", background: "#0D0D0D" }}>
            <div style={{ height: 2, background: "rgba(255,255,255,0.07)", borderRadius: 1, overflow: "hidden" }}>
              <div style={{ height: "100%", width: `${progress * 100}%`, background: "#EDE8DC", borderRadius: 1, transition: "width 0.2s linear" }} />
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6, fontFamily: "var(--font-body)", fontSize: 10, color: "#555", letterSpacing: "0.05em" }}>
              <span>+{elapsed}s</span>
              <span>−{remaining}s</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────
// MOOD BOARD PANEL
// ─────────────────────────────────────────

function MoodBoardPanel({ items, onRemove, onClose, onExportBoard }: {
  items: SearchResult[];
  onRemove: (id: string) => void;
  onClose: () => void;
  onExportBoard: () => void;
}) {
  return (
    <div style={{
      position: "fixed", top: 0, right: 0, width: 420, height: "100vh",
      background: "#0D0D0D", borderLeft: "1px solid rgba(255,255,255,0.06)",
      zIndex: 110, display: "flex", flexDirection: "column",
      animation: "slideIn 0.2s ease",
    }}>
      <div style={{
        padding: "20px 20px 14px", borderBottom: "1px solid rgba(255,255,255,0.06)",
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <span style={{ fontFamily: "var(--font-display)", fontSize: 15, color: "#F5F5F0", letterSpacing: "0.08em" }}>
          Collected Looks ({items.length})
        </span>
        <div style={{ display: "flex", gap: 8 }}>
          {items.length > 0 && (
            <button onClick={onExportBoard} style={{
              background: "rgba(237,232,220,0.08)", border: "1px solid rgba(237,232,220,0.2)",
              borderRadius: 4, padding: "4px 10px", cursor: "pointer",
              fontFamily: "var(--font-body)", fontSize: 10, color: "#EDE8DC", letterSpacing: "0.08em",
            }}>↓ Export</button>
          )}
          <button onClick={onClose} style={{ background: "none", border: "none", color: "#8A8A85", cursor: "pointer", fontSize: 18 }}>×</button>
        </div>
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: 16 }}>
        {items.length === 0 ? (
          <p style={{ fontFamily: "var(--font-body)", fontSize: 13, color: "#8A8A85", textAlign: "center", marginTop: 40 }}>
            Collect looks with ⊞ Collect to build your board.
          </p>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {items.map((item) => (
              <div key={item.moment_id} style={{ position: "relative", borderRadius: 6, overflow: "hidden", aspectRatio: "3/4", background: "#141414" }}>
                {item.thumbnail_url && <img src={item.thumbnail_url} alt={item.description} style={{ width: "100%", height: "100%", objectFit: "cover" }} />}
                <div style={{
                  position: "absolute", bottom: 0, left: 0, right: 0,
                  background: "linear-gradient(transparent, rgba(0,0,0,0.85))",
                  padding: "20px 8px 8px",
                }}>
                  <div style={{ fontFamily: "var(--font-body)", fontSize: 9, letterSpacing: "0.1em", color: "#EDE8DC", textTransform: "uppercase" }}>
                    {item.brand} · {item.season}
                  </div>
                </div>
                <button onClick={() => onRemove(item.moment_id)} style={{
                  position: "absolute", top: 5, right: 5,
                  background: "rgba(0,0,0,0.6)", border: "none", borderRadius: "50%",
                  width: 20, height: 20, cursor: "pointer", color: "#8A8A85", fontSize: 12,
                  display: "flex", alignItems: "center", justifyContent: "center",
                }}>×</button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────
// SHOW BRIEF MODAL
// ─────────────────────────────────────────

function ShowBriefModal({ show, onClose }: { show: ShowItem; onClose: () => void }) {
  const [brief, setBrief] = useState<string | null>(show.summary || null);
  const [loading, setLoading] = useState(!show.summary);

  useEffect(() => {
    if (show.summary) return;
    setLoading(true);
    fetch(`${API_URL}/api/shows/${show.id}/brief`)
      .then((r) => r.json())
      .then((d) => { setBrief(d.brief); setLoading(false); })
      .catch(() => setLoading(false));
  }, [show.id]);

  const lines = brief ? brief.split("\n").filter(Boolean) : [];

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.75)",
      zIndex: 200, display: "flex", alignItems: "center", justifyContent: "center", padding: 32,
    }} onClick={onClose}>
      <div style={{
        background: "#111", border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: 10, padding: 32, maxWidth: 560, width: "100%",
        maxHeight: "80vh", overflowY: "auto",
      }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20 }}>
          <div>
            <div style={{ fontFamily: "var(--font-display)", fontSize: 22, fontWeight: 300, color: "#F5F5F0", letterSpacing: "0.08em" }}>{show.brand}</div>
            <div style={{ fontFamily: "var(--font-body)", fontSize: 11, color: "#8A8A85", letterSpacing: "0.1em", marginTop: 2 }}>{show.season} · {show.moment_count} looks</div>
          </div>
          <button onClick={onClose} style={{ background: "none", border: "none", color: "#8A8A85", cursor: "pointer", fontSize: 20 }}>×</button>
        </div>
        {loading ? (
          <div style={{ fontFamily: "var(--font-body)", fontSize: 13, color: "#8A8A85", padding: "20px 0" }}>Generating brief…</div>
        ) : brief ? (
          <div style={{ fontFamily: "var(--font-body)", fontSize: 13, lineHeight: 1.8, color: "#C8C8C0" }}>
            {lines.map((line, i) => {
              if (line.startsWith("**") && line.includes("**")) {
                const parts = line.split("**").filter(Boolean);
                return (
                  <div key={i} style={{ marginBottom: 12 }}>
                    <span style={{ color: "#EDE8DC", fontWeight: 500 }}>{parts[0]}</span>
                    {parts[1] && <span>{parts[1]}</span>}
                  </div>
                );
              }
              if (line.startsWith("- ")) {
                return <div key={i} style={{ paddingLeft: 12, marginBottom: 4, color: "#A0A09A" }}>· {line.slice(2)}</div>;
              }
              return <div key={i} style={{ marginBottom: 8 }}>{line}</div>;
            })}
          </div>
        ) : (
          <div style={{ fontFamily: "var(--font-body)", fontSize: 13, color: "#8A8A85" }}>Could not generate brief.</div>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────
// BOOKMARK PANEL
// ─────────────────────────────────────────

function BookmarkPanel({ bookmarks, onRemove, onClose }: {
  bookmarks: SearchResult[];
  onRemove: (id: string) => void;
  onClose: () => void;
}) {
  return (
    <div style={{
      position: "fixed", top: 0, right: 0, width: 320, height: "100vh",
      background: "#111", borderLeft: "1px solid rgba(255,255,255,0.06)",
      zIndex: 100, display: "flex", flexDirection: "column",
      animation: "slideIn 0.2s ease",
    }}>
      <div style={{
        padding: "24px 20px 16px", borderBottom: "1px solid rgba(255,255,255,0.06)",
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <span style={{ fontFamily: "var(--font-display)", fontSize: 16, color: "#F5F5F0" }}>Saved ({bookmarks.length})</span>
        <button onClick={onClose} style={{ background: "none", border: "none", color: "#8A8A85", cursor: "pointer", fontSize: 18 }}>×</button>
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: 16 }}>
        {bookmarks.length === 0 ? (
          <p style={{ fontFamily: "var(--font-body)", fontSize: 13, color: "#8A8A85", textAlign: "center", marginTop: 40 }}>Nothing saved yet.</p>
        ) : (
          bookmarks.map((b) => (
            <div key={b.moment_id} style={{
              background: "#141414", borderRadius: 6, padding: "10px 12px", marginBottom: 8,
              display: "flex", justifyContent: "space-between", alignItems: "flex-start",
            }}>
              <div>
                <div style={{ fontFamily: "var(--font-body)", fontSize: 10, letterSpacing: "0.12em", color: "#EDE8DC", textTransform: "uppercase", marginBottom: 4 }}>
                  {b.brand} · {formatTimestamp(b.timestamp_start)}
                </div>
                <div style={{ fontFamily: "var(--font-display)", fontSize: 12, color: "#8A8A85", lineHeight: 1.5 }}>
                  {b.description.slice(0, 60)}…
                </div>
              </div>
              <button onClick={() => onRemove(b.moment_id)} style={{ background: "none", border: "none", color: "#555", cursor: "pointer", fontSize: 14, marginLeft: 8, flexShrink: 0 }}>×</button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────
// MAIN PAGE
// ─────────────────────────────────────────

export default function Home() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [weakMatch, setWeakMatch] = useState(false);
  const [loading, setLoading] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [processingTime, setProcessingTime] = useState<number | null>(null);
  const [bookmarks, setBookmarks] = useState<Map<string, SearchResult>>(new Map());
  const [showBookmarks, setShowBookmarks] = useState(false);
  const [moodBoard, setMoodBoard] = useState<Map<string, SearchResult>>(new Map());
  const [showMoodBoard, setShowMoodBoard] = useState(false);
  const [shows, setShows] = useState<ShowItem[]>([]);
  const [activeBrief, setActiveBrief] = useState<ShowItem | null>(null);
  const [playingMoment, setPlayingMoment] = useState<{ id: string; brand: string; season: string } | null>(null);
  const [heroResult, setHeroResult] = useState<SearchResult | null>(null);
  const [synthesis, setSynthesis] = useState<{ synthesis: string; grounded: boolean; cited_moment_ids: string[] } | null>(null);
  const [synthesizing, setSynthesizing] = useState(false);
  const [searchError, setSearchError] = useState(false);
  const debounceRef = useRef<NodeJS.Timeout>();

  // Load from localStorage on mount
  useEffect(() => {
    try {
      const saved = localStorage.getItem("fa_bookmarks");
      if (saved) setBookmarks(new Map((JSON.parse(saved) as SearchResult[]).map((r) => [r.moment_id, r])));
      const board = localStorage.getItem("fa_moodboard");
      if (board) setMoodBoard(new Map((JSON.parse(board) as SearchResult[]).map((r) => [r.moment_id, r])));
    } catch {}
  }, []);

  // Persist bookmarks + moodboard
  useEffect(() => {
    localStorage.setItem("fa_bookmarks", JSON.stringify(Array.from(bookmarks.values())));
  }, [bookmarks]);
  useEffect(() => {
    localStorage.setItem("fa_moodboard", JSON.stringify(Array.from(moodBoard.values())));
  }, [moodBoard]);

  // Load shows
  useEffect(() => {
    fetch(`${API_URL}/api/shows`).then((r) => r.json()).then((d) => setShows(d.shows || [])).catch(() => {});
  }, []);

  // Hero moment on mount
  useEffect(() => {
    fetch(`${API_URL}/api/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: HERO_QUERY, limit: 1 }),
    })
      .then((r) => r.json())
      .then((d: SearchResponse) => {
        if (d.results.length > 0 && d.results[0].thumbnail_url) {
          setHeroResult(d.results[0]);
        }
      })
      .catch(() => {});
  }, []);

  const runSearch = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults([]);
      setWeakMatch(false);
      setSynthesis(null);
      setSearchError(false);
      setHasSearched(false);
      return;
    }
    setLoading(true);
    setSynthesis(null);
    setSearchError(false);
    try {
      const searchAbort = new AbortController();
      const searchTimer = setTimeout(() => searchAbort.abort(), 10000);
      let res: Response;
      try {
        res = await fetch(`${API_URL}/api/search`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: q, limit: 20 }),
          signal: searchAbort.signal,
        });
      } finally {
        clearTimeout(searchTimer);
      }
      const data: SearchResponse = await res.json();
      setResults(data.results);
      setWeakMatch(data.results.length > 0 && data.results.every((r) => r.confidence < 75));
      setProcessingTime(data.processing_time_ms);
      setHasSearched(true);
      log("search", { query: q, results: data.total, ms: data.processing_time_ms });

      // Fire synthesis for ≥3 results — don't block the grid
      if (data.results.length >= 3) {
        setSynthesizing(true);
        const topIds = data.results.slice(0, 8).map((r) => r.moment_id);
        const synthAbort = new AbortController();
        const synthTimer = setTimeout(() => synthAbort.abort(), 15000);
        fetch(`${API_URL}/api/synthesize`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: q, moment_ids: topIds }),
          signal: synthAbort.signal,
        })
          .then((r) => r.json())
          .then((s) => {
            setSynthesis(s);
            setSynthesizing(false);
            log("synthesis_impression", { query: q, grounded: s.grounded });
          })
          .catch(() => setSynthesizing(false))
          .finally(() => clearTimeout(synthTimer));
      }
    } catch (err) {
      console.error(err);
      setSearchError(true);
      setResults([]);
      setHasSearched(true);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setQuery(val);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => runSearch(val), 300);
  };

  const handleChipClick = (chip: string) => {
    setQuery(chip);
    log("chip_click", { chip });
    runSearch(chip);
  };

  const handleBookmark = (result: SearchResult) => {
    setBookmarks((prev) => {
      const next = new Map(prev);
      if (next.has(result.moment_id)) next.delete(result.moment_id);
      else next.set(result.moment_id, result);
      return next;
    });
  };

  const handlePin = (result: SearchResult) => {
    setMoodBoard((prev) => {
      const next = new Map(prev);
      if (next.has(result.moment_id)) next.delete(result.moment_id);
      else next.set(result.moment_id, result);
      return next;
    });
  };

  const handleExportBoard = async () => {
    const items = Array.from(moodBoard.values());
    if (!items.length) return;
    const res = await fetch(`${API_URL}/api/moodboard/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ moment_ids: items.map((i) => i.moment_id), title: "Mood Board" }),
    });
    const data = await res.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `fa-moodboard-${Date.now()}.json`; a.click();
    URL.revokeObjectURL(url);
  };

  const handleExport = async (momentId: string, brand: string, ts: number, confidence?: number) => {
    try {
      const res = await fetch(`${API_URL}/api/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ moment_id: momentId, confidence }),
      });
      const data = await res.json();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `fa-export-${brand.toLowerCase()}-${Math.floor(ts)}s.json`; a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error("Export failed:", err);
    }
  };

  const bookmarkList = Array.from(bookmarks.values());
  const citedIds = new Set(synthesis?.cited_moment_ids || []);

  return (
    <>
      {/* Top bar */}
      <div style={{
        position: "fixed", top: 0, left: 0, right: 0, height: 52,
        borderBottom: hasSearched ? "1px solid rgba(255,255,255,0.05)" : "none",
        background: "rgba(10,10,10,0.9)", backdropFilter: "blur(12px)",
        zIndex: 50, display: "flex", alignItems: "center",
        padding: "0 32px", justifyContent: "space-between",
      }}>
        <span style={{
          fontFamily: "var(--font-display)", fontSize: 16,
          letterSpacing: "0.12em", color: "#EDE8DC",
          opacity: hasSearched ? 1 : 0, transition: "opacity 0.3s",
        }}>
          FASHION ARCHIVE
        </span>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {/* Looks board (mood board) toggle */}
          <button
            onClick={() => { setShowMoodBoard((v) => !v); setShowBookmarks(false); }}
            style={{
              background: moodBoard.size > 0 ? "rgba(74,222,128,0.08)" : "none",
              border: "1px solid rgba(255,255,255,0.1)", borderRadius: 4,
              padding: "4px 12px", cursor: "pointer",
              fontFamily: "var(--font-body)", fontSize: 11,
              color: moodBoard.size > 0 ? "#4ADE80" : "#8A8A85",
              letterSpacing: "0.1em", transition: "all 0.15s",
            }}
          >
            ⊞ Looks {moodBoard.size > 0 ? `(${moodBoard.size})` : ""}
          </button>

          {/* Bookmark toggle */}
          <button
            onClick={() => { setShowBookmarks((v) => !v); setShowMoodBoard(false); }}
            style={{
              background: "none", border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: 4, padding: "4px 12px", cursor: "pointer",
              fontFamily: "var(--font-body)", fontSize: 11,
              color: bookmarkList.length > 0 ? "#EDE8DC" : "#8A8A85",
              letterSpacing: "0.1em", transition: "all 0.15s",
            }}
          >
            ✦ Saved {bookmarkList.length > 0 ? `(${bookmarkList.length})` : ""}
          </button>
        </div>
      </div>

      {/* Main content */}
      <main style={{
        minHeight: "100vh",
        paddingTop: hasSearched ? 80 : 0,
        transition: "padding-top 0.4s ease",
      }}>
        {/* Hero / search section */}
        <div style={{
          display: "flex", flexDirection: "column",
          alignItems: "center",
          justifyContent: hasSearched ? "flex-start" : "center",
          minHeight: hasSearched ? "auto" : "100vh",
          padding: hasSearched ? "0 32px 24px" : "0 32px",
          transition: "all 0.4s ease",
        }}>
          {/* Empty state — wordmark + hero + chips */}
          {!hasSearched && (
            <div style={{ width: "100%", maxWidth: 960, textAlign: "center", marginBottom: 36 }}>
              <h1 style={{
                fontFamily: "var(--font-display)", fontSize: 42, fontWeight: 300,
                letterSpacing: "0.2em", color: "#F5F5F0", marginBottom: 32,
              }}>
                FASHION ARCHIVE
              </h1>

              {/* Hero moment */}
              {heroResult && (
                <HeroMoment
                  result={heroResult}
                  onPlay={(r) => setPlayingMoment({ id: r.moment_id, brand: r.brand, season: r.season })}
                />
              )}
            </div>
          )}

          {/* Search input */}
          <div style={{ width: "100%", maxWidth: 640, position: "relative" }}>
            <input
              type="text"
              value={query}
              onChange={handleInput}
              placeholder="Search the archive…"
              autoFocus
              style={{
                width: "100%", background: "#141414",
                border: "1px solid rgba(255,255,255,0.08)", borderRadius: 8,
                padding: "16px 20px 16px 48px",
                fontFamily: "var(--font-body)", fontSize: 15, color: "#F5F5F0",
                transition: "border-color 0.2s",
              }}
              onFocus={(e) => (e.target.style.borderColor = "rgba(237,232,220,0.25)")}
              onBlur={(e) => (e.target.style.borderColor = "rgba(255,255,255,0.08)")}
            />
            <span style={{
              position: "absolute", left: 16, top: "50%", transform: "translateY(-50%)",
              color: "#8A8A85", fontSize: 20, pointerEvents: "none",
            }}>⌕</span>
            {loading && (
              <span style={{
                position: "absolute", right: 16, top: "50%", transform: "translateY(-50%)",
                width: 12, height: 12,
                border: "1.5px solid rgba(237,232,220,0.3)", borderTopColor: "#EDE8DC",
                borderRadius: "50%", animation: "spin 0.6s linear infinite", display: "block",
              }} />
            )}
          </div>

          {/* Curated query chips — show when no active search */}
          {!hasSearched && (
            <div style={{
              display: "flex", gap: 8, flexWrap: "wrap",
              justifyContent: "center", marginTop: 20,
              animation: "fadeIn 0.5s ease 0.2s both",
            }}>
              {CURATED_QUERIES.map((chip) => (
                <button
                  key={chip}
                  onClick={() => handleChipClick(chip)}
                  style={{
                    background: "none",
                    border: "1px solid rgba(255,255,255,0.08)",
                    borderRadius: 100, padding: "6px 14px", cursor: "pointer",
                    fontFamily: "var(--font-body)", fontSize: 12,
                    color: "#8A8A85", letterSpacing: "0.03em",
                    transition: "color 0.15s, border-color 0.15s",
                  }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = "#EDE8DC"; (e.currentTarget as HTMLElement).style.borderColor = "rgba(237,232,220,0.25)"; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = "#8A8A85"; (e.currentTarget as HTMLElement).style.borderColor = "rgba(255,255,255,0.08)"; }}
                >
                  {chip}
                </button>
              ))}
            </div>
          )}

          {/* Result count */}
          {hasSearched && !loading && (
            <div style={{
              width: "100%", maxWidth: 640, marginTop: 10,
              fontFamily: "var(--font-body)", fontSize: 11,
              color: "#8A8A85", letterSpacing: "0.05em",
            }}>
              {results.length} result{results.length !== 1 ? "s" : ""} · {processingTime}ms
            </div>
          )}
        </div>

        {/* Search error state */}
        {hasSearched && searchError && (
          <div style={{
            maxWidth: 640, margin: "0 auto 20px", padding: "12px 20px",
            borderRadius: 6, background: "#1A0000", border: "1px solid rgba(180,60,60,0.25)",
            display: "flex", alignItems: "center", gap: 10,
            fontFamily: "var(--font-body)", fontSize: 12, color: "#8A5555", letterSpacing: "0.03em",
          }}>
            <span style={{ fontSize: 14 }}>⚠</span>
            Archive unreachable — check your connection and try again
          </div>
        )}

        {/* Weak match warning */}
        {hasSearched && weakMatch && (
          <div style={{
            maxWidth: 640, margin: "0 auto 20px", padding: "10px 16px",
            borderRadius: 6, background: "#1C1500", border: "1px solid #3D2E00",
            display: "flex", alignItems: "center", gap: 10,
            fontFamily: "var(--font-body)", fontSize: 12, color: "#FACC15", letterSpacing: "0.03em",
          }}>
            <span style={{ fontSize: 14 }}>⚠</span>
            No strong match found in the archive — showing closest results
          </div>
        )}

        {/* Synthesis line — shown ONLY when synthesis is grounded (non-null) */}
        {hasSearched && results.length > 0 && (synthesizing || synthesis?.grounded) && (
          <div style={{
            maxWidth: 1280, margin: "0 auto 24px", padding: "0 32px",
            animation: "fadeIn 0.4s ease",
          }}>
            <div style={{
              padding: "18px 24px",
              borderLeft: "2px solid #C8A97A",
              background: "rgba(200,169,122,0.04)",
              borderRadius: "0 8px 8px 0",
            }}>
              <div style={{
                fontFamily: "var(--font-body)", fontSize: 9, letterSpacing: "0.16em",
                color: "#C8A97A", textTransform: "uppercase", marginBottom: 10,
              }}>
                Intelligence
              </div>
              {synthesizing && !synthesis ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 10, paddingTop: 2 }}>
                  <div className="synth-shimmer" style={{
                    height: 18, borderRadius: 2, width: "88%",
                    background: "rgba(200,169,122,0.12)",
                  }} />
                  <div className="synth-shimmer" style={{
                    height: 18, borderRadius: 2, width: "62%",
                    background: "rgba(200,169,122,0.07)",
                    animationDelay: "0.25s",
                  }} />
                </div>
              ) : (
                <div style={{
                  fontFamily: "var(--font-display)", fontSize: 18, fontWeight: 300,
                  color: "#EDE8DC", lineHeight: 1.65, letterSpacing: "0.02em", fontStyle: "italic",
                }}>
                  {synthesis?.synthesis}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Results grid */}
        {hasSearched && results.length > 0 && (
          <div style={{
            padding: "0 32px 64px",
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))",
            gap: 16, maxWidth: 1280, margin: "0 auto",
          }}>
            {results.map((r) => (
              <ResultCard
                key={r.moment_id}
                result={r}
                bookmarks={new Set(bookmarks.keys())}
                pinned={new Set(moodBoard.keys())}
                highlighted={citedIds.has(r.moment_id)}
                onBookmark={handleBookmark}
                onPin={handlePin}
                onExport={handleExport}
                onPlay={(id) => setPlayingMoment({ id, brand: r.brand, season: r.season })}
              />
            ))}
          </div>
        )}

        {/* No results — editorial empty state, not a sad blank */}
        {hasSearched && !loading && results.length === 0 && (
          <div style={{ textAlign: "center", padding: "80px 32px 48px" }}>
            <div style={{
              fontFamily: "var(--font-display)", fontSize: 22, fontWeight: 300,
              color: "#8A8A85", marginBottom: 10, letterSpacing: "0.02em",
            }}>
              Nothing found in the archive
            </div>
            <div style={{
              fontFamily: "var(--font-body)", fontSize: 12, color: "#444",
              letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 36,
            }}>
              Try a different search, or explore the archive below
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "center" }}>
              {CURATED_QUERIES.map((chip) => (
                <button
                  key={chip}
                  onClick={() => handleChipClick(chip)}
                  style={{
                    background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)",
                    borderRadius: 20, padding: "6px 14px", cursor: "pointer",
                    fontFamily: "var(--font-body)", fontSize: 12, color: "#8A8A85",
                    letterSpacing: "0.04em", transition: "all 0.15s",
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.color = "#EDE8DC"; e.currentTarget.style.borderColor = "rgba(237,232,220,0.2)"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.color = "#8A8A85"; e.currentTarget.style.borderColor = "rgba(255,255,255,0.08)"; }}
                >
                  {chip}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Empty state — shows grid + timeline tile */}
        {!hasSearched && (
          <div style={{ padding: "0 32px 64px", maxWidth: 960, margin: "0 auto", width: "100%" }}>

            {/* Timeline destination tile */}
            <a href="/timeline" style={{ textDecoration: "none", display: "block", marginBottom: 24 }}
              onClick={() => log("timeline_click", { from: "empty_state" })}
            >
              <div style={{
                background: "#141414", border: "1px solid rgba(255,255,255,0.06)",
                borderRadius: 8, padding: "20px 24px",
                display: "flex", alignItems: "center", gap: 20,
                transition: "border-color 0.15s, background 0.15s",
              }}
                onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.borderColor = "#C8A97A"; (e.currentTarget as HTMLElement).style.background = "#181510"; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.borderColor = "rgba(255,255,255,0.06)"; (e.currentTarget as HTMLElement).style.background = "#141414"; }}
              >
                <div style={{
                  width: 48, height: 48, borderRadius: 6,
                  background: "rgba(200,169,122,0.1)",
                  border: "1px solid rgba(200,169,122,0.2)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 20, flexShrink: 0,
                }}>
                  ↗
                </div>
                <div>
                  <div style={{
                    fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 300,
                    color: "#EDE8DC", letterSpacing: "0.06em", marginBottom: 4,
                  }}>
                    Follow a house through time
                  </div>
                  <div style={{
                    fontFamily: "var(--font-body)", fontSize: 11,
                    color: "#8A8A85", letterSpacing: "0.05em",
                  }}>
                    Chanel A/W, 1993 → 2025 — track house codes across 30 years of archive
                  </div>
                </div>
                <div style={{ marginLeft: "auto", fontFamily: "var(--font-body)", fontSize: 11, color: "#C8A97A", letterSpacing: "0.1em" }}>
                  Timeline →
                </div>
              </div>
            </a>

            {/* Shows grid — diversity-sorted: one from each house, round-robin, most recent first per house */}
            {shows.length > 0 && (
              <>
                <div style={{
                  fontFamily: "var(--font-body)", fontSize: 10, letterSpacing: "0.14em",
                  color: "#8A8A85", textTransform: "uppercase", marginBottom: 12,
                }}>
                  Browse shows — click for brief
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 8 }}>
                  {diverseShows(shows.filter((s) => s.status === "ready")).map((show) => (
                    <button
                      key={show.id}
                      onClick={() => { setActiveBrief(show); log("brief_open", { show_id: show.id, brand: show.brand }); }}
                      style={{
                        background: "#141414", border: "1px solid rgba(255,255,255,0.06)",
                        borderRadius: 6, padding: "12px 14px", cursor: "pointer",
                        textAlign: "left", transition: "border-color 0.15s",
                      }}
                      onMouseEnter={(e) => (e.currentTarget.style.borderColor = "rgba(255,255,255,0.14)")}
                      onMouseLeave={(e) => (e.currentTarget.style.borderColor = "rgba(255,255,255,0.06)")}
                    >
                      <div style={{ fontFamily: "var(--font-body)", fontSize: 11, color: "#EDE8DC", letterSpacing: "0.06em", marginBottom: 3 }}>{show.brand}</div>
                      <div style={{ fontFamily: "var(--font-body)", fontSize: 10, color: "#8A8A85", letterSpacing: "0.05em" }}>{show.season} · {show.moment_count} looks</div>
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        )}
      </main>

      {/* Mood board panel */}
      {showMoodBoard && (
        <MoodBoardPanel
          items={Array.from(moodBoard.values())}
          onRemove={(id) => setMoodBoard((prev) => { const next = new Map(prev); next.delete(id); return next; })}
          onClose={() => setShowMoodBoard(false)}
          onExportBoard={handleExportBoard}
        />
      )}

      {/* Bookmark panel */}
      {showBookmarks && (
        <BookmarkPanel
          bookmarks={bookmarkList}
          onRemove={(id) => { setBookmarks((prev) => { const next = new Map(prev); next.delete(id); return next; }); }}
          onClose={() => setShowBookmarks(false)}
        />
      )}

      {/* Show brief modal */}
      {activeBrief && <ShowBriefModal show={activeBrief} onClose={() => setActiveBrief(null)} />}

      {/* Video modal */}
      {playingMoment && (
        <VideoModal
          momentId={playingMoment.id}
          brand={playingMoment.brand}
          season={playingMoment.season}
          onClose={() => setPlayingMoment(null)}
        />
      )}
    </>
  );
}
