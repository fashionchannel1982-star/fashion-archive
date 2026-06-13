/**
 * Fashion Archive — Search Page
 * Apple TV aesthetic × Google search simplicity
 * v2: confidence scores + bookmark + export
 */

import { useState, useRef, useCallback, useEffect } from "react";
import Hls from "hls.js";

// ─────────────────────────────────────────
// TYPES
// ─────────────────────────────────────────

interface SearchResult {
  moment_id: string;
  show_id: string;
  brand: string;
  season: string;
  year: number;
  timestamp_start: number;
  timestamp_end: number;
  description: string;
  thumbnail_url?: string;
  confidence: number;
  score_raw: number;
}

interface SearchResponse {
  query: string;
  results: SearchResult[];
  total: number;
  processing_time_ms: number;
}

// ─────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────

// Bands calibrated to Marengo3.0 text↔image cross-modal similarity range (0.06–0.14).
// confidence = round(similarity * 100), so range is roughly 6–14.
function confidenceLabel(c: number): string {
  if (c >= 12) return "Strong";
  if (c >= 9) return "Good";
  return "Relevant";
}

function confidenceColor(c: number): string {
  if (c >= 12) return "#4ADE80";
  if (c >= 9) return "#FACC15";
  return "#94A3B8";
}

function formatTimestamp(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// ─────────────────────────────────────────
// RESULT CARD
// ─────────────────────────────────────────

function ResultCard({
  result,
  bookmarks,
  onBookmark,
  onExport,
  onPlay,
}: {
  result: SearchResult;
  bookmarks: Set<string>;
  onBookmark: (r: SearchResult) => void;
  onExport: (momentId: string, brand: string, ts: number) => void;
  onPlay: (momentId: string) => void;
}) {
  const isBookmarked = bookmarks.has(result.moment_id);

  return (
    <div
      style={{
        background: "#141414",
        borderRadius: 8,
        overflow: "hidden",
        transition: "transform 0.2s ease, background 0.2s ease",
        cursor: "default",
      }}
      onMouseEnter={(e) =>
        (e.currentTarget.style.background = "#1C1C1C")
      }
      onMouseLeave={(e) =>
        (e.currentTarget.style.background = "#141414")
      }
    >
      {/* Thumbnail — click to play */}
      <div
        onClick={() => onPlay(result.moment_id)}
        style={{
          aspectRatio: "16/9",
          background: "#0F0F0F",
          position: "relative",
          overflow: "hidden",
          cursor: "pointer",
        }}
      >
        {result.thumbnail_url ? (
          <>
            <img
              src={result.thumbnail_url}
              alt={result.description}
              style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
            />
            {/* Play overlay — shown on hover of the outer div */}
            <div style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}>
              <div style={{
                width: 44,
                height: 44,
                borderRadius: "50%",
                background: "rgba(255,255,255,0.15)",
                backdropFilter: "blur(6px)",
                border: "1px solid rgba(255,255,255,0.2)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 15,
                color: "#F5F5F0",
                paddingLeft: 3,
                opacity: 0,
                transition: "opacity 0.18s",
              }}
                className="play-btn"
              >▶</div>
            </div>
          </>
        ) : (
          <div
            style={{
              width: "100%",
              height: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#2A2A2A",
              fontFamily: "var(--font-display)",
              fontSize: 13,
              letterSpacing: "0.2em",
            }}
          >
            {result.brand.toUpperCase()}
          </div>
        )}

        {/* Timestamp pill */}
        <div
          style={{
            position: "absolute",
            bottom: 8,
            left: 8,
            background: "rgba(0,0,0,0.75)",
            backdropFilter: "blur(4px)",
            borderRadius: 4,
            padding: "2px 8px",
            fontFamily: "var(--font-body)",
            fontSize: 11,
            color: "#F5F5F0",
            letterSpacing: "0.05em",
          }}
        >
          {formatTimestamp(result.timestamp_start)}
        </div>
      </div>

      {/* Body */}
      <div style={{ padding: "14px 16px 16px" }}>
        {/* Header row */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            marginBottom: 8,
          }}
        >
          {/* Brand pill */}
          <span
            style={{
              fontFamily: "var(--font-body)",
              fontSize: 10,
              fontWeight: 500,
              letterSpacing: "0.15em",
              color: "#EDE8DC",
              background: "rgba(237,232,220,0.08)",
              borderRadius: 3,
              padding: "2px 7px",
              textTransform: "uppercase",
            }}
          >
            {result.brand}
          </span>

          {/* Season */}
          <span
            style={{
              fontFamily: "var(--font-body)",
              fontSize: 10,
              color: "#8A8A85",
              letterSpacing: "0.05em",
            }}
          >
            {result.season}
          </span>

          {/* Spacer */}
          <div style={{ flex: 1 }} />

          {/* Confidence badge */}
          <span
            style={{
              fontFamily: "var(--font-body)",
              fontSize: 10,
              fontWeight: 500,
              color: confidenceColor(result.confidence),
              letterSpacing: "0.05em",
              display: "flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: confidenceColor(result.confidence),
                display: "inline-block",
              }}
            />
            {result.confidence}% · {confidenceLabel(result.confidence)}
          </span>
        </div>

        {/* Description */}
        <p
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 14,
            fontWeight: 300,
            color: "#F5F5F0",
            lineHeight: 1.6,
            margin: "0 0 12px",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {result.description}
        </p>

        {/* Actions */}
        <div style={{ display: "flex", gap: 8 }}>
          <button
            onClick={() => onBookmark(result)}
            title={isBookmarked ? "Remove bookmark" : "Bookmark"}
            style={{
              background: isBookmarked
                ? "rgba(237,232,220,0.12)"
                : "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: 4,
              padding: "5px 10px",
              cursor: "pointer",
              fontFamily: "var(--font-body)",
              fontSize: 11,
              color: isBookmarked ? "#EDE8DC" : "#8A8A85",
              transition: "all 0.15s",
              display: "flex",
              alignItems: "center",
              gap: 5,
            }}
          >
            {isBookmarked ? "✦ Saved" : "✦ Save"}
          </button>

          <button
            onClick={() =>
              onExport(result.moment_id, result.brand, result.timestamp_start)
            }
            title="Export as JSON"
            style={{
              background: "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: 4,
              padding: "5px 10px",
              cursor: "pointer",
              fontFamily: "var(--font-body)",
              fontSize: 11,
              color: "#8A8A85",
              transition: "all 0.15s",
              display: "flex",
              alignItems: "center",
              gap: 5,
            }}
            onMouseEnter={(e) =>
              (e.currentTarget.style.color = "#F5F5F0")
            }
            onMouseLeave={(e) =>
              (e.currentTarget.style.color = "#8A8A85")
            }
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

const MAX_CLIP_DURATION = 120; // seconds — hard cap per clip

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
  const [progress, setProgress] = useState(0); // 0–1
  const [clipDuration, setClipDuration] = useState(0);
  const startRef = useRef(0);
  const endRef = useRef(0);
  const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const res = await fetch(`${API_URL}/api/moments/${momentId}/play`);
        if (!res.ok) throw new Error(`${res.status}`);
        const data = await res.json();
        if (cancelled || !videoRef.current) return;

        const clipStart = data.timestamp_start;
        // Cap: no longer than MAX_CLIP_DURATION, and no further than timestamp_end
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

        if (Hls.isSupported()) {
          const hls = new Hls({ startPosition: clipStart });
          hlsRef.current = hls;
          hls.loadSource(data.hls_url);
          hls.attachMedia(video);
          hls.on(Hls.Events.MANIFEST_PARSED, () => {
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
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.88)",
        backdropFilter: "blur(8px)",
        zIndex: 200,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "100%",
          maxWidth: 960,
          background: "#111",
          borderRadius: 10,
          overflow: "hidden",
          border: "1px solid rgba(255,255,255,0.08)",
        }}
      >
        {/* Header */}
        <div style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "14px 20px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{
              fontFamily: "var(--font-display)",
              fontSize: 14,
              letterSpacing: "0.12em",
              color: "#EDE8DC",
            }}>
              {brand.toUpperCase()} · {season}
            </span>
            {clipDuration > 0 && (
              <span style={{
                fontFamily: "var(--font-body)",
                fontSize: 11,
                color: "#8A8A85",
                letterSpacing: "0.05em",
              }}>
                {formatTimestamp(startRef.current)} — {formatTimestamp(endRef.current)}
                {clipDuration >= MAX_CLIP_DURATION && (
                  <span style={{ color: "#555", marginLeft: 6 }}>· clipped to 2 min</span>
                )}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              color: "#8A8A85",
              cursor: "pointer",
              fontSize: 20,
              lineHeight: 1,
              padding: "0 4px",
            }}
          >
            ×
          </button>
        </div>

        {/* Video */}
        <div style={{ position: "relative", aspectRatio: "16/9", background: "#000" }}>
          {error ? (
            <div style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontFamily: "var(--font-body)",
              fontSize: 13,
              color: "#8A8A85",
            }}>
              {error}
            </div>
          ) : (
            <video
              ref={videoRef}
              controls
              style={{ width: "100%", height: "100%", display: "block" }}
            />
          )}
        </div>

        {/* Clip progress bar */}
        {clipDuration > 0 && (
          <div style={{ padding: "10px 20px 14px", background: "#0D0D0D" }}>
            <div style={{
              height: 2,
              background: "rgba(255,255,255,0.07)",
              borderRadius: 1,
              overflow: "hidden",
            }}>
              <div style={{
                height: "100%",
                width: `${progress * 100}%`,
                background: "#EDE8DC",
                borderRadius: 1,
                transition: "width 0.2s linear",
              }} />
            </div>
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              marginTop: 6,
              fontFamily: "var(--font-body)",
              fontSize: 10,
              color: "#555",
              letterSpacing: "0.05em",
            }}>
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
// BOOKMARK PANEL
// ─────────────────────────────────────────

function BookmarkPanel({
  bookmarks,
  onRemove,
  onClose,
}: {
  bookmarks: SearchResult[];
  onRemove: (id: string) => void;
  onClose: () => void;
}) {
  return (
    <div
      style={{
        position: "fixed",
        top: 0,
        right: 0,
        width: 320,
        height: "100vh",
        background: "#111",
        borderLeft: "1px solid rgba(255,255,255,0.06)",
        zIndex: 100,
        display: "flex",
        flexDirection: "column",
        animation: "slideIn 0.2s ease",
      }}
    >
      <style>{`@keyframes slideIn { from { transform: translateX(100%) } to { transform: translateX(0) } }`}</style>

      <div
        style={{
          padding: "24px 20px 16px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 16,
            color: "#F5F5F0",
          }}
        >
          Saved ({bookmarks.length})
        </span>
        <button
          onClick={onClose}
          style={{
            background: "none",
            border: "none",
            color: "#8A8A85",
            cursor: "pointer",
            fontSize: 18,
          }}
        >
          ×
        </button>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: 16 }}>
        {bookmarks.length === 0 ? (
          <p
            style={{
              fontFamily: "var(--font-body)",
              fontSize: 13,
              color: "#8A8A85",
              textAlign: "center",
              marginTop: 40,
            }}
          >
            Nothing saved yet.
          </p>
        ) : (
          bookmarks.map((b) => (
            <div
              key={b.moment_id}
              style={{
                background: "#141414",
                borderRadius: 6,
                padding: "10px 12px",
                marginBottom: 8,
                display: "flex",
                justifyContent: "space-between",
                alignItems: "flex-start",
              }}
            >
              <div>
                <div
                  style={{
                    fontFamily: "var(--font-body)",
                    fontSize: 10,
                    letterSpacing: "0.12em",
                    color: "#EDE8DC",
                    textTransform: "uppercase",
                    marginBottom: 4,
                  }}
                >
                  {b.brand} · {formatTimestamp(b.timestamp_start)}
                </div>
                <div
                  style={{
                    fontFamily: "var(--font-display)",
                    fontSize: 12,
                    color: "#8A8A85",
                    lineHeight: 1.5,
                  }}
                >
                  {b.description.slice(0, 60)}…
                </div>
              </div>
              <button
                onClick={() => onRemove(b.moment_id)}
                style={{
                  background: "none",
                  border: "none",
                  color: "#555",
                  cursor: "pointer",
                  fontSize: 14,
                  marginLeft: 8,
                  flexShrink: 0,
                }}
              >
                ×
              </button>
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

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function Home() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [processingTime, setProcessingTime] = useState<number | null>(null);
  const [bookmarks, setBookmarks] = useState<Map<string, SearchResult>>(new Map());
  const [showBookmarks, setShowBookmarks] = useState(false);
  const [playingMoment, setPlayingMoment] = useState<{ id: string; brand: string; season: string } | null>(null);
  const debounceRef = useRef<NodeJS.Timeout>();

  // Load bookmarks from localStorage on mount
  useEffect(() => {
    try {
      const saved = localStorage.getItem("fa_bookmarks");
      if (saved) {
        const parsed: SearchResult[] = JSON.parse(saved);
        setBookmarks(new Map(parsed.map((r) => [r.moment_id, r])));
      }
    } catch {}
  }, []);

  // Persist bookmarks to localStorage
  useEffect(() => {
    localStorage.setItem(
      "fa_bookmarks",
      JSON.stringify(Array.from(bookmarks.values()))
    );
  }, [bookmarks]);

  const runSearch = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults([]);
      setHasSearched(false);
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/api/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q, limit: 20 }),
      });
      const data: SearchResponse = await res.json();
      setResults(data.results);
      setProcessingTime(data.processing_time_ms);
      setHasSearched(true);
    } catch (err) {
      console.error(err);
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

  const handleBookmark = (result: SearchResult) => {
    setBookmarks((prev) => {
      const next = new Map(prev);
      if (next.has(result.moment_id)) {
        next.delete(result.moment_id);
      } else {
        next.set(result.moment_id, result);
      }
      return next;
    });
  };

  const handleExport = async (
    momentId: string,
    brand: string,
    ts: number
  ) => {
    try {
      const res = await fetch(`${API_URL}/api/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ moment_id: momentId }),
      });
      const data = await res.json();
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `fa-export-${brand.toLowerCase()}-${Math.floor(ts)}s.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error("Export failed:", err);
    }
  };

  const bookmarkList = Array.from(bookmarks.values());

  return (
    <>

      {/* Top bar */}
      <div
        style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          height: 52,
          borderBottom: hasSearched
            ? "1px solid rgba(255,255,255,0.05)"
            : "none",
          background: "rgba(10,10,10,0.9)",
          backdropFilter: "blur(12px)",
          zIndex: 50,
          display: "flex",
          alignItems: "center",
          padding: "0 32px",
          justifyContent: "space-between",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 16,
            letterSpacing: "0.12em",
            color: "#EDE8DC",
            opacity: hasSearched ? 1 : 0,
            transition: "opacity 0.3s",
          }}
        >
          FASHION ARCHIVE
        </span>

        {/* Bookmark toggle */}
        <button
          onClick={() => setShowBookmarks((v) => !v)}
          style={{
            background: "none",
            border: "1px solid rgba(255,255,255,0.1)",
            borderRadius: 4,
            padding: "4px 12px",
            cursor: "pointer",
            fontFamily: "var(--font-body)",
            fontSize: 11,
            color: bookmarkList.length > 0 ? "#EDE8DC" : "#8A8A85",
            letterSpacing: "0.1em",
            transition: "all 0.15s",
          }}
        >
          ✦ Saved {bookmarkList.length > 0 ? `(${bookmarkList.length})` : ""}
        </button>
      </div>

      {/* Main content */}
      <main
        style={{
          minHeight: "100vh",
          paddingTop: hasSearched ? 80 : 0,
          transition: "padding-top 0.4s ease",
        }}
      >
        {/* Hero / search */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: hasSearched ? "flex-start" : "center",
            minHeight: hasSearched ? "auto" : "100vh",
            padding: hasSearched ? "0 32px 24px" : "0 32px",
            transition: "all 0.4s ease",
          }}
        >
          {/* Wordmark — only on empty state */}
          {!hasSearched && (
            <div style={{ textAlign: "center", marginBottom: 40 }}>
              <h1
                style={{
                  fontFamily: "var(--font-display)",
                  fontSize: 42,
                  fontWeight: 300,
                  letterSpacing: "0.2em",
                  color: "#F5F5F0",
                  marginBottom: 8,
                }}
              >
                FASHION ARCHIVE
              </h1>
              <a
                href="/timeline"
                style={{
                  fontSize: 11,
                  letterSpacing: "0.18em",
                  color: "#8A8A85",
                  textDecoration: "none",
                  borderBottom: "1px solid #2A2A2A",
                  paddingBottom: 2,
                  transition: "color 0.2s, border-color 0.2s",
                }}
                onMouseEnter={(e) => { (e.target as HTMLElement).style.color = "#C8A97A"; (e.target as HTMLElement).style.borderBottomColor = "#C8A97A"; }}
                onMouseLeave={(e) => { (e.target as HTMLElement).style.color = "#8A8A85"; (e.target as HTMLElement).style.borderBottomColor = "#2A2A2A"; }}
              >
                CHANEL A/W TIMELINE →
              </a>
            </div>
          )}

          {/* Search input */}
          <div
            style={{
              width: "100%",
              maxWidth: 640,
              position: "relative",
            }}
          >
            <input
              type="text"
              value={query}
              onChange={handleInput}
              placeholder="Search the archive…"
              autoFocus
              style={{
                width: "100%",
                background: "#141414",
                border: "1px solid rgba(255,255,255,0.08)",
                borderRadius: 8,
                padding: "16px 20px 16px 48px",
                fontFamily: "var(--font-body)",
                fontSize: 15,
                color: "#F5F5F0",
                transition: "border-color 0.2s",
              }}
              onFocus={(e) =>
                (e.target.style.borderColor = "rgba(237,232,220,0.25)")
              }
              onBlur={(e) =>
                (e.target.style.borderColor = "rgba(255,255,255,0.08)")
              }
            />
            {/* Search icon */}
            <span
              style={{
                position: "absolute",
                left: 16,
                top: "50%",
                transform: "translateY(-50%)",
                color: "#8A8A85",
                fontSize: 20,
                pointerEvents: "none",
              }}
            >
              ⌕
            </span>

            {/* Loading indicator */}
            {loading && (
              <span
                style={{
                  position: "absolute",
                  right: 16,
                  top: "50%",
                  transform: "translateY(-50%)",
                  width: 12,
                  height: 12,
                  border: "1.5px solid rgba(237,232,220,0.3)",
                  borderTopColor: "#EDE8DC",
                  borderRadius: "50%",
                  animation: "spin 0.6s linear infinite",
                  display: "block",
                }}
              />
            )}
            <style>{`@keyframes spin { to { transform: translateY(-50%) rotate(360deg); } }`}</style>
          </div>

          {/* Result count */}
          {hasSearched && !loading && (
            <div
              style={{
                width: "100%",
                maxWidth: 640,
                marginTop: 10,
                fontFamily: "var(--font-body)",
                fontSize: 11,
                color: "#8A8A85",
                letterSpacing: "0.05em",
              }}
            >
              {results.length} result{results.length !== 1 ? "s" : ""} ·{" "}
              {processingTime}ms
            </div>
          )}
        </div>

        {/* Results grid */}
        {hasSearched && results.length > 0 && (
          <div
            style={{
              padding: "0 32px 64px",
              display: "grid",
              gridTemplateColumns:
                "repeat(auto-fill, minmax(300px, 1fr))",
              gap: 16,
              maxWidth: 1280,
              margin: "0 auto",
            }}
          >
            {results.map((r) => (
              <ResultCard
                key={r.moment_id}
                result={r}
                bookmarks={new Set(bookmarks.keys())}
                onBookmark={handleBookmark}
                onExport={handleExport}
                onPlay={(id) => setPlayingMoment({ id, brand: r.brand, season: r.season })}
              />
            ))}
          </div>
        )}

        {/* Empty state */}
        {hasSearched && !loading && results.length === 0 && (
          <div
            style={{
              textAlign: "center",
              padding: "60px 32px",
              fontFamily: "var(--font-display)",
              fontSize: 18,
              color: "#8A8A85",
              fontWeight: 300,
            }}
          >
            No results for "{query}"
          </div>
        )}
      </main>

      {/* Bookmark panel */}
      {showBookmarks && (
        <BookmarkPanel
          bookmarks={bookmarkList}
          onRemove={(id) => {
            setBookmarks((prev) => {
              const next = new Map(prev);
              next.delete(id);
              return next;
            });
          }}
          onClose={() => setShowBookmarks(false)}
        />
      )}

      {/* Video modal */}
      {playingMoment && (
        <VideoModal
          momentId={playingMoment.id}
          brand={playingMoment.brand}
          season={playingMoment.season}
          onClose={() => setPlayingMoment(null)}
        />
      )}

      <style>{`
        div:has(> .play-btn):hover .play-btn { opacity: 1 !important; }
      `}</style>
    </>
  );
}
