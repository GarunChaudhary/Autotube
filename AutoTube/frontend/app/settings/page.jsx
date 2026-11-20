"use client";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";

function StockKeys({ h, onSaved }) {
  const [pexels, setPexels] = useState("");
  const [pixabay, setPixabay] = useState("");
  const [contact, setContact] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  async function save() {
    setBusy(true); setMsg(null);
    try {
      const r = await api("/api/stock-keys", { method: "POST", body: { pexels, pixabay, wikimedia_contact: contact } });
      setMsg({ ok: true, text: `Saved and tested: ${r.saved.join(" + ")}. New videos now use real video footage.` });
      setPexels(""); setPixabay(""); setContact(""); onSaved();
    } catch (e) { setMsg({ ok: false, text: e.message }); }
    setBusy(false);
  }
  const both = h.pexels_configured && h.pixabay_configured;
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <h2>🎥 Real video footage {both && <span className="badge b-ok">on</span>}</h2>
      <p className="small muted">Real archive film of the actual event is used automatically: NASA footage always, Wikimedia newsreels once you add a contact below.
        For real-world motion footage in every scene (cities, oceans, forests, night streets, storms…) add the two free
        stock-video keys below. Each takes about 1 minute: sign up, then copy the key. No payment, no credit needed in videos.</p>
      <div className="grid2" style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))" }}>
        <div>
          <label>Pexels key {h.pexels_configured && <span className="badge b-ok">ready</span>}</label>
          <input value={pexels} onChange={(e) => setPexels(e.target.value)} placeholder={h.pexels_configured ? "saved - paste to replace" : "paste key"} />
          <div className="small muted">Get it: <a href="https://www.pexels.com/api/new/" target="_blank" rel="noreferrer">pexels.com/api/new</a> → sign up → “Your API key”.</div>
        </div>
        <div>
          <label>Pixabay key {h.pixabay_configured && <span className="badge b-ok">ready</span>}</label>
          <input value={pixabay} onChange={(e) => setPixabay(e.target.value)} placeholder={h.pixabay_configured ? "saved - paste to replace" : "paste key"} />
          <div className="small muted">Get it: <a href="https://pixabay.com/api/docs/#api_search_videos" target="_blank" rel="noreferrer">pixabay.com/api/docs</a> → log in → key is shown in the “Parameters” table.</div>
        </div>
      </div>
      <div style={{ marginTop: 12 }}>
        <label>Real archive film from Wikimedia {h.wikimedia_contact && <span className="badge b-ok">on</span>}</label>
        <input value={contact} onChange={(e) => setContact(e.target.value)} placeholder={h.wikimedia_contact ? "saved - type to replace" : "your email or website, e.g. your YouTube channel link"} />
        <div className="small muted">Optional. Wikimedia only allows video downloads (real newsreels: Titanic, Hindenburg, Apollo 11…) from apps that
          include a contact. It is sent only to Wikimedia in the request header, never shown publicly.</div>
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <button className="primary" onClick={save} disabled={busy || (!pexels.trim() && !pixabay.trim() && !contact.trim())}>{busy ? "Testing…" : "Test & save keys"}</button>
        {msg && <span className="small" style={{ color: msg.ok ? "var(--ok)" : "var(--warn)" }}>{msg.text}</span>}
      </div>
    </div>
  );
}

export default function Settings() {
  const [h, setH] = useState(null);
  const load = () => api("/api/health").then(setH);
  useEffect(() => { load(); }, []);
  if (!h) return <div className="muted">Loading…</div>;
  const Row = ({ name, ok, desc, how }) => (
    <tr><td><b>{name}</b><div className="small muted">{desc}</div></td>
      <td><span className={"badge " + (ok ? "b-ok" : "b-warn")}>{ok ? "ready" : "not set"}</span></td><td className="small">{how}</td></tr>
  );
  return (
    <>
      <div className="topbar"><h1>Providers & setup</h1></div>
      <StockKeys h={h} onSaved={load} />
      <div className="card table-wrap" style={{ marginBottom: 16 }}>
        <h2>Free providers (automatic fallback, left to right)</h2>
        <table><thead><tr><th>Provider</th><th>Status</th><th>How to enable</th></tr></thead><tbody>
          <Row name="Ollama (local AI)" ok={h.ollama.running && h.ollama.model_ready} desc={`Writer ${h.ollama_writer} (scripts, SEO); checker ${h.ollama_model} (fact-check, ideas). CPU, unlimited, free.`}
            how={<>Started automatically by START_AUTOTUBE.bat. Models: {h.ollama.models.join(", ") || "none"}</>} />
          <Row name="Gemini API (optional)" ok={h.gemini_configured} desc="Stronger, faster writing when the free-tier quota allows. Used if Ollama fails."
            how={<>Get a free key at <a href="https://aistudio.google.com/apikey" target="_blank">aistudio.google.com/apikey</a> → set GEMINI_API_KEY in .env → restart.</>} />
          <Row name="Offline template writer" ok desc="Last resort: extractive source-based script. Always routed to human review." how="Built in." />
          <Row name="Pexels (stock video)" ok={h.pexels_configured} desc="Real-world stock video, 200 requests/hour. No credit required."
            how={<>Free key at <a href="https://www.pexels.com/api/" target="_blank">pexels.com/api</a> → paste it in the Real video footage box above.</>} />
          <Row name="Pixabay (stock video)" ok={h.pixabay_configured} desc="Real-world stock video, 100 requests/minute. No credit required."
            how={<>Free key at <a href="https://pixabay.com/api/docs/" target="_blank">pixabay.com/api/docs</a> → paste it in the Real video footage box above.</>} />
          <Row name="Wikimedia Commons (film + images)" ok desc="Real archive film (newsreels, historic footage) and photos of the actual subject. Only public-domain, CC0, CC BY and CC BY-SA (no NC/ND). Credited on screen."
            how="Built in, no key needed." />
          <Row name="Openverse (images)" ok desc="CC-licensed images filtered to licences allowing commercial use and modification." how="Built in, no key needed." />
          <Row name="NASA Image Library" ok desc="Public-domain images and video for space and science topics." how="Built in, no key needed." />
          <Row name="Kokoro-82M narration" ok={h.kokoro} desc="Natural neural voice (Apache-2.0), runs locally. Piper and Windows SAPI are fallbacks." how="Downloaded by setup." />
          <Row name="FFmpeg" ok={h.ffmpeg} desc={h.ffmpeg_path} how="Installed by setup (portable)." />
          <Row name="Music & sound design" ok desc="Original score per video (mood-matched) plus original transition SFX. Best quality: add YouTube Audio Library tracks to assets/music/<mood>/." how="Built in." />
        </tbody></table>
      </div>
      <div className="card">
        <h2>Configuration file</h2>
        <p className="small">All keys live in <code>AutoTube\.env</code> (copy from <code>.env.example</code>). Restart AutoTube after editing.
          Secrets never leave your PC; the YouTube token is encrypted with a key stored in Windows Credential Manager.</p>
      </div>
    </>
  );
}
