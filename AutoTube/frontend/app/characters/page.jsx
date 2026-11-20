"use client";
import { useEffect, useRef, useState } from "react";
import { api, API, Badge, fmtTime, tzAbbrev, zonedToUtc } from "../../lib/api";
import GenrePicker from "../../components/GenrePicker";

const BLANK = { name: "", category_id: null, channel_id: null, gender: "female", age_range: "20-30", style: "realistic",
  appearance: "", personality: "", voice: "af_heart", original_confirmed: false, enabled: true, status: "design" };

export default function Characters() {
  const [d, setD] = useState(null);
  const [channels, setChannels] = useState([]);
  const [cat, setCat] = useState("all");          // selected category tab: "all" | "none" | id
  const [form, setForm] = useState(null);          // null = closed, {id?...} = editing
  const [newCat, setNewCat] = useState(null);      // null = closed, {name, icon, description}
  const [msg, setMsg] = useState(null);
  const [playing, setPlaying] = useState(null);
  const [make, setMake] = useState(null);          // null = closed, else the "make video" form
  const [tz, setTz] = useState("Asia/Kolkata");
  const audio = useRef(null);
  const formRef = useRef(null);
  const makeRef = useRef(null);

  const load = () => {
    api("/api/characters").then(setD).catch((e) => flash(e.message, true));
    api("/api/channels").then(setChannels).catch(() => {});
    api("/api/settings").then((s) => setTz(s.timezone || "Asia/Kolkata")).catch(() => {});
  };
  function openMake(c) {
    const others = d.characters.filter((x) => x.enabled && x.id !== c?.id);
    setMake({ mode: "narrator", ids: [c?.id ?? d.characters.find((x) => x.enabled)?.id ?? null, others[0]?.id ?? null],
      channel_id: c?.channel_id || channels[0]?.id || null, kind: "short", genre: "", topic: "", when: "approve",
      date: "", time: "18:00", ideas: [], busy: "" });
    setTimeout(() => makeRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  }
  useEffect(() => { load(); }, []);
  const flash = (text, err) => { setMsg({ text, err }); if (!err) setTimeout(() => setMsg(null), 2500); };

  async function saveChar() {
    const { id, category_name, category_icon, created_at, updated_at, ...body } = form;
    try {
      await api(id ? `/api/characters/${id}` : "/api/characters", { method: id ? "PUT" : "POST", body });
      flash(id ? "Character updated." : "Character created."); setForm(null); load();
    } catch (e) { flash(e.message, true); }
  }
  async function delChar(c) {
    if (!confirm(`Delete the character "${c.name}"?`)) return;
    try { await api(`/api/characters/${c.id}`, { method: "DELETE" }); flash("Character deleted."); load(); }
    catch (e) { flash(e.message, true); }
  }
  async function saveCat() {
    const { id, ...body } = newCat;
    try {
      await api(id ? `/api/character-categories/${id}` : "/api/character-categories", { method: id ? "PUT" : "POST", body });
      flash(id ? "Category updated." : "Category added."); setNewCat(null); load();
    } catch (e) { flash(e.message, true); }
  }
  async function delCat(c) {
    if (!confirm(`Delete the category "${c.name}"? Its ${c.characters} character(s) are kept, without a category.`)) return;
    try { await api(`/api/character-categories/${c.id}`, { method: "DELETE" }); setCat("all"); flash("Category deleted."); load(); }
    catch (e) { flash(e.message, true); }
  }
  function preview(voice, name) {
    if (audio.current) audio.current.pause();
    const a = new Audio(`${API}/api/characters/voice-preview?voice=${encodeURIComponent(voice)}&name=${encodeURIComponent(name || "")}`);
    audio.current = a; setPlaying(voice);
    a.onended = () => setPlaying(null);
    a.onerror = () => { setPlaying(null); flash("Voice preview failed. Is the voice engine installed?", true); };
    a.play().catch(() => setPlaying(null));
  }
  async function suggestTopics() {
    setMake((m) => ({ ...m, busy: "ideas" }));
    try {
      const r = await api(`/api/channels/${make.channel_id}/suggest-topics?count=6${make.genre ? `&genre=${make.genre}` : ""}`, { method: "POST" });
      setMake((m) => ({ ...m, ideas: r.topics, busy: "" }));
    } catch (e) { setMake((m) => ({ ...m, busy: "" })); flash(e.message, true); }
  }
  async function makeVideo() {
    const mode = d.modes.find((x) => x.id === make.mode);
    const ids = make.ids.slice(0, mode.characters);
    if (make.when === "schedule" && !make.date) return flash("Choose the date to publish.", true);
    setMake((m) => ({ ...m, busy: "make" }));
    try {
      const v = await api("/api/videos", { method: "POST", body: {
        channel_id: Number(make.channel_id), kind: make.kind, topic: make.topic, genre: make.genre || null,
        character_ids: ids, character_mode: make.mode, upload_immediately: make.when === "approve",
        scheduled_for: make.when === "schedule" ? zonedToUtc(make.date, make.time, tz) : null } });
      setMake(null); load();
      setMsg({ text: <>Video #{v.id} “{v.topic}” is being made now. {make.when === "schedule" ? `It goes public on YouTube at ${make.date} ${make.time} ${tzAbbrev(tz)}.` : "It uploads to YouTube as soon as it's ready."}{v.upload_warning ? ` Note: ${v.upload_warning}.` : ""} <a href={`/video/?id=${v.id}`}>Watch progress →</a></> });
    } catch (e) { setMake((m) => ({ ...m, busy: "" })); flash(e.message, true); }
  }
  function openForm(c) {
    setForm(c ? { ...c, original_confirmed: !!c.original_confirmed, enabled: !!c.enabled,
      voice: voicesFor(c.gender).includes(c.voice) ? c.voice : freeVoice(c.gender, c.id) }   // fix a voice of the wrong gender
      : { ...BLANK, voice: freeVoice(BLANK.gender, null), category_id: typeof cat === "number" ? cat : null });
    setTimeout(() => formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  }

  if (!d) return <div className="muted">Loading…</div>;
  const shown = d.characters.filter((c) => cat === "all" || (cat === "none" ? !c.category_id : c.category_id === cat));
  const uncategorised = d.characters.filter((c) => !c.category_id).length;
  const selCat = d.categories.find((c) => c.id === cat);
  const set = (k, v) => setForm({ ...form, [k]: v });
  // voices that fit a gender; the first one not used by another character comes first
  const voicesFor = (g) => Object.keys(d.voices).filter((v) => g === "non-binary" || d.voice_genders[v] === g);
  const freeVoice = (g, selfId) => {
    const taken = new Set(d.characters.filter((c) => c.id !== selfId).map((c) => c.voice));
    const pool = voicesFor(g);
    return pool.find((v) => !taken.has(v)) || pool[0];
  };
  const setGender = (g) => setForm({ ...form, gender: g,
    voice: voicesFor(g).includes(form.voice) ? form.voice : freeVoice(g, form.id) });
  const voiceClash = form && d.characters.find((c) => c.id !== form.id && c.voice === form.voice);

  return (
    <>
      <div className="topbar"><h1>AI Characters</h1>
        <div className="row">
          <button onClick={() => setNewCat({ name: "", icon: "", description: "" })}>+ Add category</button>
          <button onClick={() => openForm(null)}>+ New character</button>
          <button className="primary" onClick={() => openMake(null)} disabled={!d.characters.some((c) => c.enabled)}
            title={d.characters.length ? "" : "Create a character first"}>🎬 Make video</button>
        </div>
      </div>
      {msg && <div className={"alert" + (msg.err ? " err" : "")}>{msg.text}</div>}

      <div className={"alert " + (d.engine.can_generate_video ? "" : "warn")}>
        <b>{d.engine.can_generate_video ? "Video generation is on." : "Design mode: video generation is not connected yet."}</b>{" "}
        {d.engine.message}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Categories</h2>
        <div className="genres">
          <button className={"genre" + (cat === "all" ? " on" : "")} onClick={() => setCat("all")}>
            <b>⭐ All characters</b><span className="small muted">{d.characters.length} total</span></button>
          {d.categories.map((c) => (
            <button key={c.id} className={"genre" + (cat === c.id ? " on" : "")} onClick={() => setCat(c.id)}>
              <b>{c.icon} {c.name}</b><span className="small muted">{c.characters} character{c.characters === 1 ? "" : "s"}{c.builtin ? "" : " · custom"}</span>
            </button>))}
          {uncategorised > 0 && <button className={"genre" + (cat === "none" ? " on" : "")} onClick={() => setCat("none")}>
            <b>No category</b><span className="small muted">{uncategorised}</span></button>}
        </div>
        {selCat && <div className="row" style={{ marginTop: 12 }}>
          <span className="small muted">{selCat.description || "No description."}</span><span className="spacer" />
          <button onClick={() => setNewCat({ id: selCat.id, name: selCat.name, icon: selCat.icon, description: selCat.description })}>Edit category</button>
          <button onClick={() => delCat(selCat)}>Delete category</button>
        </div>}
        {newCat && <div className="card" style={{ marginTop: 12, background: "var(--panel-2)" }}>
          <h2>{newCat.id ? "Edit category" : "New category"}</h2>
          <div className="grid g3">
            <div><label>Name</label><input value={newCat.name} placeholder="e.g. Cooking show" onChange={(e) => setNewCat({ ...newCat, name: e.target.value })} /></div>
            <div><label>Icon (emoji, optional)</label><input value={newCat.icon} maxLength={8} placeholder="🍳" onChange={(e) => setNewCat({ ...newCat, icon: e.target.value })} /></div>
            <div><label>Description</label><input value={newCat.description} placeholder="What videos this is for" onChange={(e) => setNewCat({ ...newCat, description: e.target.value })} /></div>
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="primary" onClick={saveCat}>{newCat.id ? "Save" : "Add category"}</button>
            <button onClick={() => setNewCat(null)}>Cancel</button></div>
        </div>}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>{cat === "all" ? "All characters" : cat === "none" ? "Characters without a category" : `${selCat?.icon || ""} ${selCat?.name || ""}`}</h2>
        {shown.length === 0 && <div className="muted">No characters here yet. <a href="#" onClick={(e) => { e.preventDefault(); openForm(null); }}>Create your first character</a>.</div>}
        <div className="grid g3">
          {shown.map((c) => (
            <div key={c.id} className="card" style={{ boxShadow: "none" }}>
              <div className="row"><b style={{ fontSize: 16 }}>{c.name}</b><span className="spacer" />
                <span className={"badge " + (c.status === "approved" ? "b-ok" : "b-info")}>{c.status === "approved" ? "Approved" : "Design"}</span></div>
              <div className="small muted" style={{ margin: "4px 0 8px" }}>
                {c.category_name ? `${c.category_icon} ${c.category_name}` : "No category"} · {d.styles[c.style]} · {c.gender}, {c.age_range}
                {!c.enabled && " · disabled"}</div>
              <div className="small" style={{ marginBottom: 6 }}>{c.appearance}</div>
              {c.personality && <div className="small muted" style={{ marginBottom: 8 }}>Personality: {c.personality}</div>}
              <div className="small muted" style={{ marginBottom: 10 }}>Voice: {d.voices[c.voice] || c.voice}</div>
              <div className="row">
                <button className="primary" onClick={() => openMake(c)} disabled={!c.enabled}>🎬 Make video</button>
                <button onClick={() => preview(c.voice, c.name)} disabled={!d.engine.can_preview_voice}>{playing === c.voice ? "🔊 Playing…" : "▶ Hear voice"}</button>
                <button onClick={() => openForm(c)}>Edit</button>
                <button onClick={() => delChar(c)}>Delete</button>
              </div>
            </div>))}
        </div>
      </div>

      {make && (() => {
        const mode = d.modes.find((x) => x.id === make.mode);
        const usable = d.characters.filter((c) => c.enabled);
        const setM = (k, v) => setMake({ ...make, [k]: v });
        const setId = (i, v) => { const ids = [...make.ids]; ids[i] = v ? Number(v) : null; setM("ids", ids); };
        const ready = mode.available && make.channel_id && make.ids.slice(0, mode.characters).every(Boolean)
          && new Set(make.ids.slice(0, mode.characters)).size === mode.characters;
        return (
          <div className="card" ref={makeRef} style={{ marginBottom: 16 }}>
            <h2>🎬 Make a video with your characters</h2>
            <label>1. Video type</label>
            <div className="genres">
              {d.modes.map((m) => (
                <button key={m.id} className={"genre" + (make.mode === m.id ? " on" : "")}
                  onClick={() => setM("mode", m.id)} style={m.available ? {} : { opacity: 0.7 }}>
                  <b>{{ narrator: "🎤", podcast: "🎙️", talking_face: "🗣️", dance: "💃" }[m.id]} {m.label}</b>
                  <span className="small muted">{{
                    narrator: "Your character tells the story in their voice and personality, over real licensed footage.",
                    podcast: "Two characters talk it through, taking turns with their own voices, over real footage.",
                    talking_face: "Your character's face on screen, lip-synced. Needs a GPU (not connected).",
                    dance: "Your character dances to a motion video you own. Needs a GPU (not connected).",
                  }[m.id]}</span>
                </button>))}
            </div>

            {!mode.available && <div className="alert warn" style={{ marginTop: 12 }}>
              <b>{mode.label} needs a GPU, which is not connected yet.</b> This PC has no graphics card, so it can't draw a moving,
              lip-synced or dancing character (one 5-second clip would take hours). To switch it on: create a Vast.ai or RunPod
              account, add about $10 credit, and ask Claude to connect it. Meanwhile, choose <b>Narrator</b> or <b>Podcast</b> -
              they work now with your character's voice.</div>}
            <div className="grid g3" style={{ marginTop: 12 }}>
              {Array.from({ length: mode.characters }).map((_, i) => (
                <div key={i}><label>{mode.characters > 1 ? `${i + 1}. ${i === 0 ? "First host (opens)" : "Second host"}` : "2. Character"}</label>
                  <select value={make.ids[i] ?? ""} onChange={(e) => setId(i, e.target.value)}>
                    <option value="">Choose…</option>
                    {usable.map((c) => <option key={c.id} value={c.id}>{c.name} · {d.voices[c.voice]?.split(" - ")[0] || c.voice}</option>)}
                  </select></div>))}
              <div><label>Format</label>
                <select value={make.kind} onChange={(e) => setM("kind", e.target.value)}>
                  <option value="short">Short (vertical, ~45 s)</option>
                  <option value="long">Long video (16:9)</option></select></div>
              {channels.length > 1 && <div><label>YouTube channel profile</label>
                <select value={make.channel_id ?? ""} onChange={(e) => setM("channel_id", Number(e.target.value))}>
                  {channels.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></div>}
            </div>
            {mode.characters === 2 && make.ids[0] && make.ids[1] && make.ids[0] === make.ids[1] &&
              <div className="alert warn small" style={{ marginTop: 8 }}>Choose two different characters.</div>}
            {usable.length < mode.characters &&
              <div className="alert warn small" style={{ marginTop: 8 }}>You need {mode.characters} characters for this. <a href="#" onClick={(e) => { e.preventDefault(); openForm(null); }}>Create another character</a>.</div>}

            <label style={{ marginTop: 12 }}>3. Story type (optional) - sets music, visuals and title style</label>
            <GenrePicker value={make.genre} onChange={(g) => setMake({ ...make, genre: g, ideas: [] })} />

            <label>4. Topic (leave empty and AutoTube picks a fresh, verified one)</label>
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <input value={make.topic} onChange={(e) => setM("topic", e.target.value)} placeholder="e.g. The Tunguska event" />
              <button onClick={suggestTopics} disabled={!!make.busy || !make.channel_id}>{make.busy === "ideas" ? "Researching…" : "Suggest topics"}</button>
            </div>
            {make.ideas.length > 0 && <div style={{ marginTop: 6 }}>{make.ideas.map((t) => (
              <div key={t} className="row" style={{ padding: "3px 0" }}><span>{t}</span><span className="spacer" />
                <button onClick={() => setM("topic", t)}>Use</button></div>))}</div>}

            <label style={{ marginTop: 12 }}>5. When to put it on YouTube</label>
            <div className="toggle"><input type="radio" name="when" checked={make.when === "approve"} onChange={() => setM("when", "approve")} />
              <div><b>Make it now and upload to YouTube as soon as it's ready</b><div className="small muted">No approval step. Only if a quality check fails does it wait for you on the Upload queue page.</div></div></div>
            <div className="toggle"><input type="radio" name="when" checked={make.when === "schedule"} onChange={() => setM("when", "schedule")} />
              <div><b>Schedule a time</b><div className="small muted">Made now, then goes public automatically at this time ({tzAbbrev(tz)}). No approval step.</div>
                {make.when === "schedule" && <div className="row" style={{ marginTop: 6 }}>
                  <input type="date" value={make.date} onChange={(e) => setM("date", e.target.value)} style={{ maxWidth: 170 }} />
                  <input type="time" value={make.time} onChange={(e) => setM("time", e.target.value)} style={{ maxWidth: 130 }} /></div>}
              </div></div>

            <div className="alert small" style={{ marginTop: 12 }}>The script is researched and fact-checked from Wikipedia like every
              AutoTube video, written in your character's personality, and voiced with their voice. Visuals are real licensed footage;
              the character's face is not shown until a GPU is connected.</div>
            <div className="row" style={{ marginTop: 12 }}>
              <button className="primary" onClick={makeVideo} disabled={!ready || !!make.busy}>{make.busy === "make" ? "Starting…" : "🎬 Make video"}</button>
              {!ready && <span className="small muted">{!mode.available ? `${mode.label} needs a GPU - choose Narrator or Podcast.`
                : `Choose ${mode.characters === 2 ? "two different characters" : "a character"} above.`}</span>}
              <button onClick={() => setMake(null)}>Cancel</button></div>
          </div>);
      })()}

      {d.videos.length > 0 && <div className="card" style={{ marginBottom: 16 }}>
        <h2>Videos with your characters</h2>
        <div className="table-wrap"><table>
          <thead><tr><th>#</th><th>Topic</th><th>Type</th><th>Characters</th><th>Status</th><th>Publish at</th><th></th></tr></thead>
          <tbody>{d.videos.map((v) => {
            let ids = []; try { ids = JSON.parse(v.character_ids || "[]"); } catch { }
            const names = ids.map((i) => d.characters.find((c) => c.id === i)?.name || `#${i}`).join(" & ");
            return (<tr key={v.id}><td>{v.id}</td><td>{v.title || v.topic}</td>
              <td>{v.kind === "short" ? "Short" : "Long"} · {d.modes.find((m) => m.id === v.character_mode)?.label || v.character_mode}</td>
              <td>{names}</td><td><Badge s={v.status} /></td><td>{v.scheduled_for ? fmtTime(v.scheduled_for, tz) : "as soon as ready"}</td>
              <td>{v.youtube_url ? <a href={v.youtube_url} target="_blank" rel="noreferrer">YouTube</a> : <a href={`/video/?id=${v.id}`}>Open</a>}</td></tr>);
          })}</tbody></table></div>
      </div>}

      {form && <div className="card" ref={formRef} style={{ marginBottom: 16 }}>
        <h2>{form.id ? `Edit ${form.name}` : "New character"}</h2>
        <div className="grid g3">
          <div><label>Name</label><input value={form.name} placeholder="e.g. Maya" onChange={(e) => set("name", e.target.value)} /></div>
          <div><label>Category</label>
            <select value={form.category_id ?? ""} onChange={(e) => set("category_id", e.target.value ? Number(e.target.value) : null)}>
              <option value="">No category</option>
              {d.categories.map((c) => <option key={c.id} value={c.id}>{c.icon} {c.name}</option>)}
            </select></div>
          <div><label>Style</label>
            <select value={form.style} onChange={(e) => set("style", e.target.value)}>
              {Object.entries(d.styles).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select></div>
          <div><label>Gender</label>
            <select value={form.gender} onChange={(e) => setGender(e.target.value)}>
              {d.genders.map((g) => <option key={g} value={g}>{g}</option>)}</select></div>
          <div><label>Age</label>
            <select value={form.age_range} onChange={(e) => set("age_range", e.target.value)}>
              {d.age_ranges.map((g) => <option key={g} value={g}>{g}</option>)}</select></div>
          <div><label>Voice</label>
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <select value={form.voice} onChange={(e) => set("voice", e.target.value)}>
                {voicesFor(form.gender).map((k) => {
                  const user = d.characters.find((c) => c.id !== form.id && c.voice === k);
                  return <option key={k} value={k}>{d.voices[k]}{user ? ` (used by ${user.name})` : ""}</option>;
                })}</select>
              <button onClick={() => preview(form.voice, form.name)} disabled={!d.engine.can_preview_voice} title="Hear this voice">▶</button>
            </div>
            <div className="small muted">{voicesFor(form.gender).length} {form.gender === "non-binary" ? "" : form.gender + " "}voices</div>
            {voiceClash && <div className="small" style={{ color: "var(--warn)" }}>{voiceClash.name} already has this voice - pick another so they sound different.</div>}
          </div>
        </div>
        <div style={{ marginTop: 10 }}><label>Appearance</label>
          <textarea rows={3} value={form.appearance} onChange={(e) => set("appearance", e.target.value)}
            placeholder="e.g. Indian woman, shoulder-length wavy black hair, warm smile, round glasses, mustard hoodie, cosy studio with plants" /></div>
        <div style={{ marginTop: 10 }}><label>Personality (optional)</label>
          <input value={form.personality} onChange={(e) => set("personality", e.target.value)} placeholder="e.g. curious, funny, asks sharp questions" /></div>
        <div className="grid g3" style={{ marginTop: 10 }}>
          {channels.length > 1 && <div><label>YouTube channel profile</label>
            <select value={form.channel_id ?? ""} onChange={(e) => set("channel_id", e.target.value ? Number(e.target.value) : null)}>
              <option value="">Any</option>
              {channels.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></div>}
          <div><label>Status</label>
            <select value={form.status} onChange={(e) => set("status", e.target.value)}>
              <option value="design">Design (still working on it)</option><option value="approved">Approved for videos</option></select></div>
        </div>
        <div className="toggle" style={{ marginTop: 10 }}><input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} />
          <div><b>Enabled</b><div className="small muted">Disabled characters are kept but not used in videos.</div></div></div>
        <div className="toggle"><input type="checkbox" checked={form.original_confirmed} onChange={(e) => set("original_confirmed", e.target.checked)} />
          <div><b>This character is original</b><div className="small muted">It does not copy or look like a celebrity or any real person.
            Look-alikes are deepfakes and YouTube removes them. Realistic AI people also get YouTube's "altered or synthetic content" label.</div></div></div>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" onClick={saveChar}>{form.id ? "Save changes" : "Create character"}</button>
          <button onClick={() => setForm(null)}>Cancel</button></div>
      </div>}
    </>
  );
}
