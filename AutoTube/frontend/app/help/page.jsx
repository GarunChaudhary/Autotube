"use client";
import { useState } from "react";

const A = ({ href, children }) => <a href={href} target={href.startsWith("http") ? "_blank" : undefined} rel="noreferrer">{children}{href.startsWith("http") ? " ↗" : ""}</a>;

const GUIDES = [
  {
    id: "connect", title: "Connect a new YouTube channel",
    body: (
      <>
        <p className="small muted">About 5 minutes. Do this on the AutoTube PC at <b>http://localhost:8000</b>. Your Google app is already set up, so you don't need a new one.</p>
        <h3>A. Before you start (one time)</h3>
        <ol className="wizard">
          <li><b>Create the new channel</b> if it doesn't exist yet: <A href="https://www.youtube.com/account">youtube.com/account</A> → <i>Add or manage your channel(s)</i> → <i>Create a channel</i>.
            A <b>brand channel</b> can live under the same Gmail.</li>
          <li><b>Let the new account sign in:</b> open <A href="https://console.cloud.google.com/auth/audience">Google Auth Platform → Audience</A>.
            If it says <b>Testing</b>, click <b>Publish app</b> so it says <b>In production</b>. Alternatively, add the new Gmail under <b>Test users</b>; in Testing mode Google logs you out every 7 days.</li>
          <li><b>Turn on the YouTube Analytics API</b> (needed for watch hours and monetization progress):{" "}
            <A href="https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com">enable it here</A>.</li>
        </ol>
        <h3>B. Connect it in AutoTube</h3>
        <ol className="wizard">
          <li>Open <A href="/youtube/">YouTube → Accounts</A>.</li>
          <li>Click <b>+ Add YouTube account</b>, enter a <b>Profile name</b> and a <b>Niche</b>, then click <b>Create and connect YouTube</b>.
            Or click <b>Connect</b> on an existing profile that has no channel yet.</li>
          <li>On Google's page:
            <ol type="a">
              <li>Pick the Gmail account.</li>
              <li>On <i>“Choose an account or brand account”</i>, pick the <b>new channel</b>.</li>
              <li>If you see <i>“Google hasn't verified this app”</i>: <b>Advanced</b> → <b>Go to AutoTube (unsafe)</b>. It's your own app.</li>
              <li><b>Tick every permission box</b>, then <b>Continue</b>.</li>
            </ol></li>
          <li>Back in AutoTube the card shows <b>Connected</b> with the channel name. Click <b>Test</b> to confirm.</li>
        </ol>
        <h3>C. After connecting (required, or nothing is made for it)</h3>
        <ol className="wizard">
          <li><A href="/channel/">Channel & niche</A>: pick the new profile and set its audience, tone and long-video length.</li>
          <li><A href="/scheduler/">Scheduler</A> → <b>Time slots</b> → <b>Add slot</b> and <b>select the new channel</b> in the channel box.
            Slots with no channel belong to your first channel.</li>
          <li><A href="/scheduler/">Scheduler</A> → <b>Daily production</b>: raise the <b>Daily total limit</b>. It covers <b>all channels together</b>, e.g. 2 channels × 4 = 8.</li>
          <li>Make sure <b>Automatic video generation</b> and <b>Automatic uploading</b> are on (Scheduler → Automation).</li>
          <li>Check it: <A href="/my-youtube/">My YouTube</A> → pick the new channel at the top. You can also make one test video from <A href="/upload-now/">Upload now</A>.</li>
        </ol>
        <h3>What happens automatically after connecting</h3>
        <ul>
          <li>Login is saved encrypted. AutoTube checks it every 45 minutes and warns you if it needs reconnecting.</li>
          <li>Each video uploads only to its own channel. One YouTube channel can be linked to one profile only, so there are no double uploads.</li>
          <li>Each channel has its own slots and daily limits. Growth learning (best genres, titles, hooks) runs separately per channel.</li>
          <li>My YouTube, Analytics and monetization progress can each show the new channel. Question comments and subscribe prompts are added to its videos too.</li>
          <li>All channels share one daily YouTube quota (10,000 units). The Dashboard shows how much is left.</li>
        </ul>
        <h3>Problems</h3>
        <ul>
          <li><b>“Access blocked / Error 403”</b>: the app is still in Testing mode. Redo step A2.</li>
          <li><b>“Error 400: redirect_uri_mismatch”</b>: open AutoTube at http://localhost:8000 on the PC, not from another device.</li>
          <li><b>“Already linked to another profile”</b>: you picked your existing channel. Reconnect and choose the new one.</li>
          <li><b>Wrong channel connected</b>: click <b>Disconnect</b> on that card, then <b>Connect</b> again.</li>
        </ul>
      </>),
  },
  {
    id: "iphone", title: "Put AutoTube on your iPhone",
    body: (
      <>
        <p className="small muted">AutoTube keeps running on this PC; the iPhone app is a remote control. The PC must stay on.</p>
        <h3>1. Get a phone link</h3>
        <ol className="wizard">
          <li>On the PC, double-click <b>PHONE_LINK.bat</b> in the AutoTube folder and keep its window open.</li>
          <li>It prints a link like <span className="mono">https://….trycloudflare.com</span>. Send that link to your phone.</li>
          <li><b>If the link shows “Error 530”</b>, this PC's network is blocking Cloudflare's tunnel (outbound port 7844). Ask the network admin to allow it,
            or use ngrok or a home/server install instead.</li>
        </ol>
        <h3>2. Install it</h3>
        <ol className="wizard">
          <li>Open the link in <b>Safari</b>; other browsers can't add apps on iPhone.</li>
          <li>Log in with your AutoTube username and password.</li>
          <li>Tap <b>Share</b> (the square with an arrow) → <b>Add to Home Screen</b> → <b>Add</b>.</li>
          <li>Open <b>AutoTube</b> from your home screen. It runs full-screen with a bottom tab bar.</li>
        </ol>
        <p className="small muted">The free Cloudflare link changes every time PHONE_LINK.bat starts. When it changes, delete the old icon and add the new link again.</p>
      </>),
  },
  {
    id: "daily", title: "Daily automation checklist",
    body: (
      <ol className="wizard">
        <li><A href="/scheduler/">Scheduler</A>: <b>Automatic video generation</b> and <b>Automatic uploading</b> are on, and each channel has its time slots.</li>
        <li>Each slot's video starts production <b>60 minutes before</b> its time and uploads automatically at the slot time, with no approval needed for scheduled videos.</li>
        <li><A href="/queue/">Upload queue</A>: see what's being made now and what's waiting.</li>
        <li><A href="/my-youtube/">My YouTube</A> and <A href="/analytics/">Analytics</A>: check views, the best genres and monetization progress.</li>
        <li>In YouTube Studio, <b>pin the question comment</b> AutoTube posts on each new video; the API can't pin it.</li>
        <li>Keep the PC on and awake. Nothing runs while it's off, and missed work catches up when it starts again.</li>
      </ol>),
  },
];

export default function Help() {
  const [open, setOpen] = useState("connect");
  return (
    <>
      <div className="topbar"><h1>Help & guides</h1></div>
      {GUIDES.map((g) => (
        <div key={g.id} className="card" style={{ marginBottom: 12 }}>
          <button onClick={() => setOpen(open === g.id ? null : g.id)} aria-expanded={open === g.id}
            style={{ width: "100%", justifyContent: "space-between", border: 0, background: "none", padding: 0, fontSize: 16, fontWeight: 700 }}>
            <span>{g.title}</span><span aria-hidden="true">{open === g.id ? "−" : "+"}</span>
          </button>
          {open === g.id && <div className="guide" style={{ marginTop: 12 }}>{g.body}</div>}
        </div>))}
    </>
  );
}
