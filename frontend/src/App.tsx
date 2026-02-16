import { useState } from "react";
import "./App.css";

interface LootItem {
  item: string;
  min: number;
  max: number;
}

interface MobData {
  name: string;
  health: number;
  attack_damage: number;
  abilities: string[];
  loot: LootItem[];
}

type AppState = "idle" | "loading" | "result" | "error";

function App() {
  const [prompt, setPrompt] = useState("");
  const [state, setState] = useState<AppState>("idle");
  const [mob, setMob] = useState<MobData | null>(null);
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState(false);

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    if (!prompt.trim()) return;

    setState("loading");
    setError("");
    setMob(null);

    try {
      const res = await fetch("/generate-json", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: prompt.trim() }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail ?? `Server error (${res.status})`);
      }
      const data: MobData = await res.json();
      setMob(data);
      setState("result");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setState("error");
    }
  }

  async function handleDownload() {
    if (!mob) return;
    setDownloading(true);
    try {
      const res = await fetch("/build-addon", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(mob),
      });
      if (!res.ok) throw new Error(`Download failed (${res.status})`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${mob.name.toLowerCase().replace(/\s+/g, "_")}.mcaddon`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Download failed");
    } finally {
      setDownloading(false);
    }
  }

  function handleStartOver() {
    setPrompt("");
    setMob(null);
    setError("");
    setState("idle");
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>AI Mob Creator</h1>
        <p className="subtitle">
          Describe a Minecraft mob and get a Bedrock addon
        </p>
      </header>

      <main className="app-main">
        {(state === "idle" || state === "error") && (
          <form className="prompt-form" onSubmit={handleGenerate}>
            <textarea
              className="prompt-input"
              placeholder='e.g. "Fire dragon boss with lava attack and diamond loot"'
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
            />
            {error && <p className="error-message">{error}</p>}
            <button
              type="submit"
              className="btn btn-primary"
              disabled={!prompt.trim()}
            >
              Generate Mob
            </button>
          </form>
        )}

        {state === "loading" && (
          <div className="loading">
            <div className="spinner" />
            <p className="loading-text">Generating your mob...</p>
          </div>
        )}

        {state === "result" && mob && (
          <div className="result">
            <div className="mob-card">
              <h2 className="mob-name">{mob.name}</h2>

              <div className="stats">
                <div className="stat-badge">
                  <span className="stat-label">Health</span>
                  <span className="stat-value">{mob.health}</span>
                </div>
                <div className="stat-badge">
                  <span className="stat-label">Attack</span>
                  <span className="stat-value">{mob.attack_damage}</span>
                </div>
              </div>

              {mob.abilities.length > 0 && (
                <div className="section">
                  <h3>Abilities</h3>
                  <div className="abilities">
                    {mob.abilities.map((a, i) => (
                      <span key={i} className="ability-pill">
                        {a}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {mob.loot.length > 0 && (
                <div className="section">
                  <h3>Loot Table</h3>
                  <ul className="loot-list">
                    {mob.loot.map((l, i) => (
                      <li key={i}>
                        {l.item}{" "}
                        <span className="loot-range">
                          x{l.min}–{l.max}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            {error && <p className="error-message">{error}</p>}

            <div className="actions">
              <button
                className="btn btn-primary"
                onClick={handleDownload}
                disabled={downloading}
              >
                {downloading ? "Downloading..." : "Download .mcaddon"}
              </button>
              <button className="btn btn-secondary" onClick={handleStartOver}>
                Start Over
              </button>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
