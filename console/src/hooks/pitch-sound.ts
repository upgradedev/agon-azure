/*
  Pitch Sound: the replay as sound, made in the browser with the Web Audio API
  and started only by an explicit tap (browsers refuse audio before one).
  - a soft tick whose tempo follows the pressing in the current 5-minute
    window (from the match analysis bands, both clubs' pressure events);
  - the tick pans left to right with the ball, so a home attack moves right;
  - an earcon on every shot, and a rising three-note figure on a goal.
  Under prefers-reduced-motion the continuous tick stays off and only the
  earcons play. Mute silences everything without stopping the replay.
  No file is fetched and nothing is sent anywhere.
*/
import { useEffect, useRef, useState } from "react";

interface Engine {
  ctx: AudioContext;
  out: GainNode;
  pan: StereoPannerNode;
  timer: number | null;
  bpm: number;
}

function blip(engine: Engine, freq: number, at: number, length = 0.06, level = 0.18): void {
  const osc = engine.ctx.createOscillator();
  const gain = engine.ctx.createGain();
  osc.type = "sine";
  osc.frequency.setValueAtTime(freq, at);
  gain.gain.setValueAtTime(0.0001, at);
  gain.gain.exponentialRampToValueAtTime(level, at + 0.01);
  gain.gain.exponentialRampToValueAtTime(0.0001, at + length);
  osc.connect(gain);
  gain.connect(engine.pan);
  osc.start(at);
  osc.stop(at + length + 0.02);
}

export interface PitchSound {
  running: boolean;
  ticking: boolean;
  muted: boolean;
  supported: boolean;
  start: () => void;
  stop: () => void;
  toggleMute: () => void;
  /* ball x in metres (0 to 105), pressure events in the window, ticks on or off */
  update: (ballX: number | null, pressure: number | null, ticks: boolean) => void;
  earcon: (kind: "shot" | "goal") => void;
}

export function usePitchSound(): PitchSound {
  const engine = useRef<Engine | null>(null);
  const [running, setRunning] = useState(false);
  const [ticking, setTicking] = useState(false);
  const [muted, setMuted] = useState(false);
  /* Decided after hydration, so the prerendered button and the first client render agree. */
  const [supported, setSupported] = useState(false);
  useEffect(() => {
    setSupported(typeof window.AudioContext === "function");
  }, []);

  function clearTimer(e: Engine): void {
    if (e.timer !== null) {
      window.clearInterval(e.timer);
      e.timer = null;
    }
    setTicking(false);
  }

  function schedule(e: Engine, bpm: number): void {
    clearTimer(e);
    e.bpm = bpm;
    e.timer = window.setInterval(() => blip(e, 880, e.ctx.currentTime + 0.01, 0.04, 0.08), Math.round(60000 / bpm));
    setTicking(true);
  }

  function start(): void {
    if (!supported || engine.current) {
      return;
    }
    const ctx = new AudioContext();
    const out = ctx.createGain();
    out.gain.value = muted ? 0 : 0.6;
    const pan = ctx.createStereoPanner();
    pan.connect(out);
    out.connect(ctx.destination);
    engine.current = { ctx, out, pan, timer: null, bpm: 0 };
    void ctx.resume();
    setRunning(true);
  }

  function stop(): void {
    const e = engine.current;
    if (!e) {
      return;
    }
    clearTimer(e);
    void e.ctx.close();
    engine.current = null;
    setRunning(false);
  }

  function toggleMute(): void {
    setMuted((m) => {
      const next = !m;
      const e = engine.current;
      if (e) {
        e.out.gain.setValueAtTime(next ? 0 : 0.6, e.ctx.currentTime);
      }
      return next;
    });
  }

  function update(ballX: number | null, pressure: number | null, ticks: boolean): void {
    const e = engine.current;
    if (!e) {
      return;
    }
    if (ballX !== null) {
      e.pan.pan.setTargetAtTime(Math.max(-1, Math.min(1, (ballX / 105) * 2 - 1)), e.ctx.currentTime, 0.1);
    }
    if (!ticks || pressure === null) {
      clearTimer(e);
      e.bpm = 0;
      return;
    }
    const bpm = Math.min(180, 60 + pressure * 12);
    if (bpm !== e.bpm) {
      schedule(e, bpm);
    }
  }

  function earcon(kind: "shot" | "goal"): void {
    const e = engine.current;
    if (!e) {
      return;
    }
    const t = e.ctx.currentTime + 0.02;
    if (kind === "goal") {
      blip(e, 523.25, t, 0.16, 0.25);
      blip(e, 659.25, t + 0.14, 0.16, 0.25);
      blip(e, 783.99, t + 0.28, 0.3, 0.25);
    } else {
      blip(e, 440, t, 0.12, 0.2);
    }
  }

  /* A hidden tab freezes the replay, so Pitch Sound stops rather than ticking on unseen. */
  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === "hidden" && engine.current) {
        const e = engine.current;
        clearTimer(e);
        void e.ctx.close();
        engine.current = null;
        setRunning(false);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(
    () => () => {
      const e = engine.current;
      if (e) {
        clearTimer(e);
        void e.ctx.close();
        engine.current = null;
      }
    },
    [],
  );

  return { running, ticking, muted, supported, start, stop, toggleMute, update, earcon };
}
