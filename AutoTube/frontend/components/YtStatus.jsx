"use client";
import { useState } from "react";
import { api, fmtTime } from "../lib/api";

export const VERIFY_URL = "https://www.youtube.com/verify";

export function PrivacyBadge({ v }) {
  if (!v.youtube_video_id) return <span className="badge">{(v.target_privacy || "public")} (planned)</span>;
  const p = v.yt_privacy || v.privacy;
  const cls = p === "public" ? "b-ok" : p === "unlisted" ? "b-info" : "b-err";
  return <span className={"badge " + cls} title={v.yt_checked_at ? `confirmed by YouTube ${fmtTime(v.yt_checked_at)}` : ""}>YouTube: {p || "unknown"}</span>;
}

export function ThumbStatus({ v }) {
  if (!v.youtube_video_id || !v.thumbnail_status) return null;
  if (v.thumbnail_status === "set") return <span className="small" style={{ color: "var(--ok)" }}>✓ custom thumbnail set</span>;
  return <span className="small" style={{ color: "var(--warn)" }} title={v.thumbnail_error || ""}>Custom thumbnail unavailable</span>;
}

/** Actions on an uploaded video. onMsg({text, err}) reports results; onChange reloads. */
export function YtActions({ v, onMsg, onChange, canManage = true }) {
  const [busy, setBusy] = useState("");
  if (!v.youtube_video_id) return null;
  const notPublic = (v.yt_privacy || v.privacy) !== "public";
  async function run(kind, path, confirmText) {
    if (confirmText && !confirm(confirmText)) return;
    setBusy(kind);
    try {
      const r = await api(path, { method: "POST" });
      onMsg?.({ text: kind === "publish" ? `YouTube confirms the video is now ${r.privacy}.` :
        kind === "thumb" ? "YouTube confirms the custom thumbnail is set." : `Checked ${r.checked} video(s) on YouTube.` });
    } catch (e) { onMsg?.({ err: true, text: e.message }); }
    setBusy(""); onChange?.();
  }
  return (
    <div className="row" style={{ gap: 6 }}>
      {notPublic && <button className="primary" disabled={!!busy} title={!canManage ? "Reconnect YouTube to grant the 'manage videos' permission" : ""}
        onClick={() => run("publish", `/api/videos/${v.id}/publish-now`, `Make "${v.title}" PUBLIC on YouTube now? (updates the existing video, no re-upload)`)}>
        {busy === "publish" ? "Publishing…" : "Publish now"}</button>}
      {v.thumbnail_status && v.thumbnail_status !== "set" &&
        <button disabled={!!busy} onClick={() => run("thumb", `/api/videos/${v.id}/thumbnail`)}>{busy === "thumb" ? "Trying…" : "Retry thumbnail"}</button>}
      <button disabled={!!busy} onClick={() => run("refresh", `/api/youtube/refresh-status?video_id=${v.id}`)}>{busy === "refresh" ? "Checking…" : "Refresh status"}</button>
    </div>
  );
}

export function ThumbnailHelp({ error }) {
  return (
    <div className="alert warn small">
      <b>Custom thumbnail unavailable.</b> {error && <>Google said: <code>{error}</code>. </>}
      If your channel is not eligible for custom thumbnails, verify your account and enable the required YouTube features:
      {" "}<a href={VERIFY_URL} target="_blank">{VERIFY_URL}</a>. Publishing continues without it; the thumbnail is kept on this PC, so press
      <b> Retry thumbnail</b> after verifying.
    </div>
  );
}
