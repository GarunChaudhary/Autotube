"use client";
import { useEffect } from "react";

export default function Automation() {
  useEffect(() => { location.replace("/scheduler/"); }, []);
  return <div className="muted">Automation settings moved to <a href="/scheduler/">Scheduler</a>.</div>;
}
