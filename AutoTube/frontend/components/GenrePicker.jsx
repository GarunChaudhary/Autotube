"use client";
import { useEffect, useState } from "react";
import { api } from "../lib/api";

// Story genre selector. value "" = channel default style (no genre).
export default function GenrePicker({ value, onChange, allowNone = true }) {
  const [genres, setGenres] = useState([]);
  const [err, setErr] = useState("");
  useEffect(() => { api("/api/genres").then((r) => setGenres(r.genres)).catch((e) => setErr(e.message)); }, []);
  if (err) return <div className="alert err">Could not load genres: {err}</div>;
  return (
    <div className="genres">
      {allowNone && (
        <button type="button" className={"genre" + (!value ? " on" : "")} onClick={() => onChange("")}>
          <b>✨ Channel default</b><span className="small muted">Use the channel's own niche and tone</span>
        </button>
      )}
      {genres.map((g) => (
        <button type="button" key={g.key} className={"genre" + (value === g.key ? " on" : "")} onClick={() => onChange(g.key)}>
          <b>{g.emoji} {g.label}</b><span className="small muted">{g.pitch}</span>
        </button>
      ))}
    </div>
  );
}
