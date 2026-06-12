import React, { useEffect, useState, useRef, useCallback } from "react";
import Head from "next/head";
import Link from "next/link";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const CODES = [
  { key: "tweed", label: "Tweed / Bouclé" },
  { key: "two_tone", label: "Two-Tone" },
  { key: "camellia", label: "Camellia" },
  { key: "pearls", label: "Pearls" },
  { key: "chains", label: "Chains" },
  { key: "quilting", label: "Quilting" },
];

const CD_COLORS: Record<string, string> = {
  "Karl Lagerfeld": "#C8A97A",
  "Virginie Viard": "#8AB4C8",
  "Chanel creative studio": "#A0C8A0",
};

interface RepMoment {
  moment_id: string;
  timestamp_start: number;
  timestamp_end: number;
  description: string;
  thumbnail_url: string | null;
}

interface TimelinePoint {
  show_id: string;
  season: string;
  year: number;
  show_date: string | null;
  creative_director: string | null;
  is_cd_transition: boolean;
  source: string;
  look_count: number;
  codes: Record<string, { count: number; pct: number }>;
  rep_moment: RepMoment | null;
}

interface EchoMoment {
  moment_id: string;
  season: string;
  year: number;
  timestamp_start: number;
  description: string;
  thumbnail_url: string | null;
  similarity?: number;
}

interface TimelineResponse {
  house: string;
  season_type: string;
  total: number;
  codes_available: string[];
  points: TimelinePoint[];
  cross_year_echo: { anchor: EchoMoment; echo: EchoMoment } | null;
}

function useTimeline(code: string | null) {
  const [data, setData] = useState<TimelineResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    const url = `${API}/api/timeline?house=Chanel&season_type=AW-RTW${code ? `&code=${code}` : ""}`;
    fetch(url)
      .then((r) => r.json())
      .then((d) => { setData(d); setLoading(false); })
      .catch((e) => { setError(e.message); setLoading(false); });
  }, [code]);

  return { data, loading, error };
}

function BarCell({ pct, code }: { pct: number; code: string }) {
  const colors: Record<string, string> = {
    tweed: "#C8A97A", two_tone: "#8AB4C8", camellia: "#E8A0A8",
    pearls: "#E8E0D0", chains: "#B8C8A8", quilting: "#C8B8D8",
  };
  const color = colors[code] || "#888";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 60 }}>
      <div style={{
        width: `${Math.max(pct * 0.8, pct > 0 ? 4 : 0)}px`,
        height: 10,
        background: color,
        borderRadius: 2,
        opacity: pct > 0 ? 0.85 : 0.15,
        transition: "width 0.4s ease",
        minWidth: pct > 0 ? 4 : 0,
      }} />
      <span style={{ fontSize: 11, color: pct > 0 ? "#F5F5F0" : "#444", minWidth: 26 }}>
        {pct > 0 ? `${pct}%` : "—"}
      </span>
    </div>
  );
}

function CDMarker({ cd }: { cd: string | null }) {
  if (!cd) return null;
  const color = CD_COLORS[cd] || "#888";
  return (
    <span style={{
      display: "inline-block",
      width: 8, height: 8,
      borderRadius: "50%",
      background: color,
      marginRight: 6,
      flexShrink: 0,
    }} />
  );
}

function TransitionFlag() {
  return (
    <span style={{
      fontSize: 9,
      letterSpacing: "0.1em",
      color: "#C8A97A",
      border: "1px solid #C8A97A55",
      borderRadius: 2,
      padding: "1px 4px",
      marginLeft: 6,
    }}>CD TRANSITION</span>
  );
}

function EchoPanel({ echo }: { echo: { anchor: EchoMoment; echo: EchoMoment } }) {
  return (
    <div style={{
      background: "#141414",
      border: "1px solid #2A2A2A",
      borderRadius: 8,
      padding: "20px 24px",
      marginTop: 32,
    }}>
      <div style={{ fontSize: 11, letterSpacing: "0.15em", color: "#8A8A85", marginBottom: 16 }}>
        CROSS-YEAR ECHO
      </div>
      <div style={{ display: "flex", gap: 24, alignItems: "flex-start" }}>
        {[echo.anchor, echo.echo].map((m, i) => (
          <React.Fragment key={m.moment_id}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 11, color: "#8A8A85", marginBottom: 6 }}>
                {m.season} · {m.timestamp_start.toFixed(0)}s
                {i === 1 && echo.echo.similarity != null && (
                  <span style={{ marginLeft: 8, color: "#4ADE80" }}>
                    {(echo.echo.similarity * 100).toFixed(0)}% match
                  </span>
                )}
              </div>
              <div style={{
                background: "#0A0A0A",
                borderRadius: 6,
                aspectRatio: "16/9",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                marginBottom: 8,
                overflow: "hidden",
              }}>
                {m.thumbnail_url ? (
                  <img src={m.thumbnail_url} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                ) : (
                  <span style={{ fontSize: 11, color: "#444" }}>{m.season}</span>
                )}
              </div>
              <p style={{ fontSize: 12, color: "#8A8A85", lineHeight: 1.5, margin: 0 }}>
                {m.description || "—"}
              </p>
            </div>
            {i === 0 && (
              <div style={{
                display: "flex",
                alignItems: "center",
                paddingTop: 48,
                color: "#444",
                fontSize: 20,
              }}>↔</div>
            )}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

function ScrubTimeline({
  points,
  activeIdx,
  onSelect,
  selectedCode,
}: {
  points: TimelinePoint[];
  activeIdx: number;
  onSelect: (i: number) => void;
  selectedCode: string | null;
}) {
  return (
    <div style={{ position: "relative", paddingBottom: 8 }}>
      {/* Connector line */}
      <div style={{
        position: "absolute",
        top: 20,
        left: 16,
        right: 16,
        height: 1,
        background: "#2A2A2A",
        zIndex: 0,
      }} />

      <div style={{ display: "flex", justifyContent: "space-between", position: "relative", zIndex: 1 }}>
        {points.map((pt, i) => {
          const isActive = i === activeIdx;
          const cdColor = pt.creative_director ? CD_COLORS[pt.creative_director] : "#888";
          const codePct = selectedCode ? (pt.codes[selectedCode]?.pct || 0) : 0;
          const hasCd = pt.is_cd_transition;

          return (
            <div
              key={pt.season}
              style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6, cursor: "pointer" }}
              onClick={() => onSelect(i)}
            >
              {/* Node */}
              <div style={{
                width: isActive ? 18 : 12,
                height: isActive ? 18 : 12,
                borderRadius: "50%",
                background: isActive ? cdColor : "#1C1C1C",
                border: `2px solid ${cdColor}`,
                transition: "all 0.2s",
                boxShadow: isActive ? `0 0 10px ${cdColor}55` : "none",
                position: "relative",
              }}>
                {hasCd && (
                  <div style={{
                    position: "absolute",
                    top: -6, left: -6, right: -6, bottom: -6,
                    borderRadius: "50%",
                    border: "1px solid #C8A97A66",
                  }} />
                )}
              </div>

              {/* Year label */}
              <span style={{
                fontSize: 10,
                color: isActive ? "#F5F5F0" : "#8A8A85",
                letterSpacing: "0.05em",
                fontWeight: isActive ? 500 : 400,
              }}>
                {pt.year}
              </span>

              {/* Code bar (shown when a code is selected) */}
              {selectedCode && (
                <div style={{
                  width: 4,
                  height: Math.max(codePct * 0.4, codePct > 0 ? 4 : 0),
                  background: "#C8A97A",
                  borderRadius: 2,
                  opacity: 0.7,
                  minHeight: 0,
                  transition: "height 0.3s",
                }} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function TimelinePage() {
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [activeIdx, setActiveIdx] = useState(0);
  const { data, loading, error } = useTimeline(selectedCode);

  const activePoint = data?.points[activeIdx] ?? null;

  // Reset active when data changes
  useEffect(() => {
    if (data?.points.length) setActiveIdx(0);
  }, [data?.points.length]);

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (!data?.points.length) return;
    if (e.key === "ArrowRight") setActiveIdx((i) => Math.min(i + 1, data.points.length - 1));
    if (e.key === "ArrowLeft") setActiveIdx((i) => Math.max(i - 1, 0));
  }, [data]);

  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  return (
    <>
      <Head>
        <title>Chanel A/W Timeline — Fashion Archive</title>
        <link
          href="https://fonts.googleapis.com/css2?family=Cormorant:wght@300;400;500&family=Space+Grotesk:wght@300;400;500&display=swap"
          rel="stylesheet"
        />
      </Head>

      <div style={{
        minHeight: "100vh",
        background: "#0A0A0A",
        color: "#F5F5F0",
        fontFamily: "'Space Grotesk', sans-serif",
        padding: "40px 48px",
        maxWidth: 1280,
        margin: "0 auto",
      }}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 48 }}>
          <div>
            <Link href="/" style={{ fontSize: 11, color: "#8A8A85", letterSpacing: "0.15em", textDecoration: "none" }}>
              ← SEARCH
            </Link>
            <h1 style={{
              fontFamily: "'Cormorant', serif",
              fontSize: 36,
              fontWeight: 300,
              margin: "12px 0 4px",
              letterSpacing: "0.02em",
            }}>
              Chanel A/W Timeline
            </h1>
            <p style={{ fontSize: 12, color: "#8A8A85", margin: 0, letterSpacing: "0.05em" }}>
              10 seasons · 2016–2025 · Ready-to-wear
            </p>
          </div>

          {/* CD Legend */}
          <div style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-end" }}>
            {Object.entries(CD_COLORS).map(([name, color]) => (
              <div key={name} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontSize: 11, color: "#8A8A85" }}>{name}</span>
                <div style={{ width: 8, height: 8, borderRadius: "50%", background: color }} />
              </div>
            ))}
            <div style={{ fontSize: 10, color: "#444", marginTop: 4 }}>◯ = CD transition</div>
          </div>
        </div>

        {loading && (
          <div style={{ color: "#8A8A85", fontSize: 13 }}>Loading timeline…</div>
        )}
        {error && (
          <div style={{ color: "#EF4444", fontSize: 13 }}>Error: {error}</div>
        )}

        {data && !loading && (
          <>
            {data.total < 10 && (
              <div style={{
                background: "#1C1400",
                border: "1px solid #C8A97A44",
                borderRadius: 6,
                padding: "10px 16px",
                marginBottom: 24,
                fontSize: 12,
                color: "#C8A97A",
              }}>
                {data.total} / 10 shows indexed — ingestion in progress for remaining shows.
              </div>
            )}

            {/* Code selector */}
            <div style={{ marginBottom: 32 }}>
              <div style={{ fontSize: 11, letterSpacing: "0.15em", color: "#8A8A85", marginBottom: 12 }}>
                SELECT HOUSE CODE
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button
                  onClick={() => setSelectedCode(null)}
                  style={{
                    padding: "6px 14px",
                    borderRadius: 4,
                    border: selectedCode === null ? "1px solid #EDE8DC" : "1px solid #2A2A2A",
                    background: selectedCode === null ? "#EDE8DC" : "transparent",
                    color: selectedCode === null ? "#0A0A0A" : "#8A8A85",
                    fontSize: 12,
                    cursor: "pointer",
                    fontFamily: "'Space Grotesk', sans-serif",
                    letterSpacing: "0.05em",
                  }}
                >
                  All
                </button>
                {CODES.map((c) => (
                  <button
                    key={c.key}
                    onClick={() => setSelectedCode(selectedCode === c.key ? null : c.key)}
                    style={{
                      padding: "6px 14px",
                      borderRadius: 4,
                      border: selectedCode === c.key ? "1px solid #C8A97A" : "1px solid #2A2A2A",
                      background: selectedCode === c.key ? "#C8A97A22" : "transparent",
                      color: selectedCode === c.key ? "#C8A97A" : "#8A8A85",
                      fontSize: 12,
                      cursor: "pointer",
                      fontFamily: "'Space Grotesk', sans-serif",
                      letterSpacing: "0.05em",
                    }}
                  >
                    {c.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Scrub timeline */}
            <div style={{
              background: "#141414",
              border: "1px solid #2A2A2A",
              borderRadius: 8,
              padding: "24px 32px",
              marginBottom: 32,
            }}>
              <ScrubTimeline
                points={data.points}
                activeIdx={activeIdx}
                onSelect={setActiveIdx}
                selectedCode={selectedCode}
              />
              <p style={{ fontSize: 11, color: "#444", margin: "16px 0 0", textAlign: "center" }}>
                Click a year to inspect · ← → arrow keys to scrub
              </p>
            </div>

            {/* Active point detail */}
            {activePoint && (
              <div style={{
                display: "grid",
                gridTemplateColumns: selectedCode && activePoint.rep_moment ? "1fr 1fr" : "1fr",
                gap: 24,
                marginBottom: 32,
              }}>
                {/* Show metadata */}
                <div style={{
                  background: "#141414",
                  border: "1px solid #2A2A2A",
                  borderRadius: 8,
                  padding: "24px",
                }}>
                  <div style={{ display: "flex", alignItems: "center", marginBottom: 4 }}>
                    <CDMarker cd={activePoint.creative_director} />
                    <span style={{
                      fontFamily: "'Cormorant', serif",
                      fontSize: 24,
                      fontWeight: 300,
                    }}>
                      Chanel {activePoint.season}
                    </span>
                    {activePoint.is_cd_transition && <TransitionFlag />}
                  </div>
                  <div style={{ fontSize: 12, color: "#8A8A85", marginBottom: 20 }}>
                    {activePoint.creative_director || "—"}
                    {activePoint.show_date && ` · ${activePoint.show_date.slice(0, 7)}`}
                    {" · "}{activePoint.look_count} looks
                    {activePoint.source === "youtube_mvp" && (
                      <span style={{ marginLeft: 8, color: "#444", fontSize: 10 }}>YT_MVP</span>
                    )}
                  </div>

                  {/* Code bars */}
                  <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                    {CODES.map((c) => {
                      const stat = activePoint.codes[c.key];
                      const isSelected = selectedCode === c.key;
                      return (
                        <div
                          key={c.key}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 12,
                            opacity: selectedCode && !isSelected ? 0.4 : 1,
                            transition: "opacity 0.2s",
                          }}
                        >
                          <span style={{
                            fontSize: 11,
                            color: isSelected ? "#C8A97A" : "#8A8A85",
                            width: 90,
                            letterSpacing: "0.04em",
                            fontWeight: isSelected ? 500 : 400,
                          }}>
                            {c.label}
                          </span>
                          <BarCell pct={stat?.pct ?? 0} code={c.key} />
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* Representative moment for selected code */}
                {selectedCode && activePoint.rep_moment && (
                  <div style={{
                    background: "#141414",
                    border: "1px solid #2A2A2A",
                    borderRadius: 8,
                    padding: "24px",
                  }}>
                    <div style={{ fontSize: 11, letterSpacing: "0.15em", color: "#8A8A85", marginBottom: 16 }}>
                      {CODES.find(c => c.key === selectedCode)?.label.toUpperCase()} · REPRESENTATIVE LOOK
                    </div>
                    <div style={{
                      background: "#0A0A0A",
                      borderRadius: 6,
                      aspectRatio: "16/9",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      marginBottom: 12,
                      overflow: "hidden",
                    }}>
                      {activePoint.rep_moment.thumbnail_url ? (
                        <img
                          src={activePoint.rep_moment.thumbnail_url}
                          style={{ width: "100%", height: "100%", objectFit: "cover" }}
                        />
                      ) : (
                        <span style={{ fontSize: 11, color: "#444" }}>
                          {activePoint.season} · {activePoint.rep_moment.timestamp_start.toFixed(0)}s
                        </span>
                      )}
                    </div>
                    <p style={{ fontSize: 12, color: "#8A8A85", lineHeight: 1.5, margin: 0 }}>
                      {activePoint.rep_moment.description || "—"}
                    </p>
                  </div>
                )}
              </div>
            )}

            {/* Cross-year echo */}
            {data.cross_year_echo && <EchoPanel echo={data.cross_year_echo} />}

            {/* All shows grid — code heatmap */}
            {selectedCode && (
              <div style={{ marginTop: 32 }}>
                <div style={{ fontSize: 11, letterSpacing: "0.15em", color: "#8A8A85", marginBottom: 16 }}>
                  {CODES.find(c => c.key === selectedCode)?.label.toUpperCase()} ACROSS ALL SEASONS
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 12 }}>
                  {data.points.map((pt, i) => {
                    const stat = pt.codes[selectedCode!];
                    const pct = stat?.pct ?? 0;
                    return (
                      <div
                        key={pt.season}
                        onClick={() => setActiveIdx(i)}
                        style={{
                          background: pct > 0 ? `rgba(200, 169, 122, ${pct / 100 * 0.4 + 0.05})` : "#141414",
                          border: activeIdx === i ? "1px solid #C8A97A" : "1px solid #2A2A2A",
                          borderRadius: 6,
                          padding: "12px 16px",
                          cursor: "pointer",
                          transition: "all 0.2s",
                        }}
                      >
                        <div style={{ fontSize: 12, color: "#F5F5F0", marginBottom: 4 }}>{pt.season}</div>
                        <div style={{ fontSize: 20, fontWeight: 500, color: pct > 30 ? "#C8A97A" : pct > 0 ? "#8A8A85" : "#2A2A2A" }}>
                          {pct > 0 ? `${pct}%` : "—"}
                        </div>
                        <div style={{ fontSize: 10, color: "#444", marginTop: 2 }}>{stat?.count ?? 0} looks</div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}
