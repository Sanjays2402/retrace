"use client";
import { useEffect, useState } from "react";
import { flushSync } from "react-dom";
import {
  Play,
  Pause,
  RotateCcw,
  ArrowRight,
  ArrowUpRight,
  Check,
  Database,
  GitBranch,
  FileText,
  ShieldCheck,
  ChevronRight,
  Terminal,
  Download,
  Copy,
  CircleStop,
  Server,
  LockKeyhole,
  Clock3,
  Moon,
  Sun,
  Palette,
  Search,
  BookOpen,
} from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { snapshot, nextPhase, filterJournal } from "@/lib/recovery";
import { SignalLab } from "@/app/signal-lab";
const repo = "https://github.com/Sanjays2402/retrace";
const siteBase = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
const docsBase = `${siteBase}/docs`;
const command =
  "python -m pip install git+https://github.com/Sanjays2402/retrace.git";
const icons = [FileText, ShieldCheck, Database, ArrowUpRight];
const phaseLabels = [
  "Run created",
  "Read started",
  "Read committed",
  "Validation committed",
  "Worker interrupted",
  "New worker claimed",
  "Summary committed",
  "Run completed",
];
const phaseFocus = [0, 0, 0, 1, 2, 2, 2, 3];
const accentChoices = ["green", "red", "yellow", "blue"] as const;
type Accent = (typeof accentChoices)[number];
export default function Home() {
  const [phase, setPhase] = useState(0),
    [selected, setSelected] = useState(0),
    [playing, setPlaying] = useState(false),
    [copied, setCopied] = useState(""),
    [onlySelected, setOnlySelected] = useState(false);
  const [showExport, setShowExport] = useState(false);
  const [exportMessage, setExportMessage] = useState("");
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [accent, setAccent] = useState<Accent>("green");
  const [journalQuery, setJournalQuery] = useState("");
  const run = snapshot(phase),
    task = run.tasks[selected];
  useEffect(() => {
    let saved: string | null = null;
    try {
      saved = localStorage.getItem("retrace-theme");
    } catch {
      // Private browsing can disable storage; the theme still works for this visit.
    }
    const preferred =
      saved === "light" || saved === "dark"
        ? saved
        : window.matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light";
    setTheme(preferred);
    document.documentElement.dataset.theme = preferred;
    try {
      const savedAccent = localStorage.getItem("retrace-accent");
      if (accentChoices.some((choice) => choice === savedAccent)) {
        setAccent(savedAccent as Accent);
        document.documentElement.dataset.accent = savedAccent as Accent;
      }
    } catch {
      // The palette still works for this visit when storage is unavailable.
    }
  }, []);
  function chooseAccent(next: Accent) {
    setAccent(next);
    document.documentElement.dataset.accent = next;
    try {
      localStorage.setItem("retrace-accent", next);
    } catch {
      // The selected palette remains active for this visit.
    }
  }
  function toggleTheme() {
    const next = theme === "light" ? "dark" : "light";
    setTheme(next);
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("retrace-theme", next);
    } catch {
      // Keep the visible theme when storage is unavailable.
    }
  }
  useEffect(() => {
    if (!playing) return;
    if (phase === 4 || phase === 7) {
      setPlaying(false);
      return;
    }
    const timer = setTimeout(() => setPhase(nextPhase), 850);
    return () => clearTimeout(timer);
  }, [playing, phase]);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(""), 2200);
    return () => clearTimeout(timer);
  }, [copied]);
  useEffect(() => {
    const context = (
      document as unknown as {
        modelContext?: {
          registerTool: (
            tool: object,
            options: { signal: AbortSignal },
          ) => void | Promise<void>;
        };
      }
    ).modelContext;
    if (!context?.registerTool) return;
    const lifecycle = new AbortController();
    try {
      Promise.resolve(
        context.registerTool(
          {
            name: "set_recovery_demo_phase",
            description:
              "Set this synthetic recovery walkthrough to a phase 0–7. No real jobs are executed.",
            inputSchema: {
              type: "object",
              properties: {
                phase: { type: "integer", minimum: 0, maximum: 7 },
              },
              required: ["phase"],
              additionalProperties: false,
            },
            annotations: { readOnlyHint: false, untrustedContentHint: false },
            execute(input: unknown) {
              if (
                !input ||
                typeof input !== "object" ||
                Object.keys(input).length !== 1 ||
                !("phase" in input) ||
                typeof input.phase !== "number"
              )
                throw new Error("Expected one numeric phase");
              const result = snapshot(input.phase);
              flushSync(() => {
                setPlaying(false);
                setPhase(result.phase);
              });
              return result;
            },
          },
          { signal: lifecycle.signal },
        ),
      ).catch(() => {});
    } catch {
      /* The visible controls work in browsers without this optional API. */
    }
    return () => lifecycle.abort();
  }, []);
  function play() {
    if (phase === 7) {
      setPhase(0);
      setSelected(0);
    } else if (phase === 4) {
      setPhase(5);
      setSelected(2);
    }
    setPlaying(true);
  }
  function reset() {
    setPlaying(false);
    setPhase(0);
    setSelected(0);
  }
  function seek(next: number) {
    setPlaying(false);
    setPhase(next);
    setSelected(phaseFocus[next]);
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied("Copied");
    } catch {
      setCopied("Select the command to copy");
    }
  }
  function download() {
    const blob = new Blob(
      [JSON.stringify({ simulation: true, ...run }, null, 2)],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "retrace-sample-run.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }
  const journal = filterJournal(
    run.events,
    onlySelected ? task.key : null,
    journalQuery,
  );
  return (
    <main className="shell">
      <aside className="sidebar">
        <a className="brand" href={`${siteBase}/`}>
          <img
            src={`${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/mark.svg`}
            alt=""
            width="38"
            height="38"
          />
          retrace<span>α</span>
        </a>
        <div className="side-label">PLAYGROUND</div>
        <div className="nav-item active">
          <GitBranch size={17} /> Recovery lab <span className="live-dot" />
        </div>
        <a className="nav-item" href="#signals">
          <Clock3 size={17} /> Signals lab <ArrowRight size={14} />
        </a>
        <a className="nav-item" href={`${docsBase}/`}>
          <BookOpen size={17} /> Documentation <ArrowRight size={14} />
        </a>
        <a className="nav-item" href={`${docsBase}/getting-started/`}>
          <FileText size={17} /> Quickstart <ArrowRight size={14} />
        </a>
        <a className="nav-item" href={`${docsBase}/architecture/`}>
          <Database size={17} /> Architecture <ArrowRight size={14} />
        </a>
        <div className="side-note">
          <span className="side-label">THE RECOVERY CONTRACT</span>
          <h2>
            The process stops.
            <br />
            The progress stays.
          </h2>
          <p>Committed steps are reused. Interrupted work runs again.</p>
          <a href={`${docsBase}/recovery/`}>
            Read the guarantees <ArrowRight size={14} />
          </a>
        </div>
        <div className="side-footer">LOCAL-FIRST · OPEN SOURCE</div>
      </aside>
      <div className="content">
        <header className="topbar">
          <a className="mobile-brand" href={`${siteBase}/`} aria-label="Retrace home">
            <img
              src={`${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/mark.svg`}
              alt=""
              width="27"
              height="27"
            />
            retrace
          </a>
          <div className="breadcrumb">
            Playground <ChevronRight size={14} />
            <strong>Recovery lab</strong>
          </div>
          <div className="topbar-actions">
            <div className="palette-control" role="group" aria-label="Accent color">
              <Palette size={15} aria-hidden="true" />
              {accentChoices.map((choice) => (
                <button
                  key={choice}
                  className={`palette-swatch ${choice}`}
                  type="button"
                  aria-label={`${choice[0].toUpperCase()}${choice.slice(1)} theme`}
                  aria-pressed={accent === choice}
                  title={`${choice[0].toUpperCase()}${choice.slice(1)} theme`}
                  onClick={() => chooseAccent(choice)}
                />
              ))}
            </div>
            <button
              className="theme-toggle"
              type="button"
              onClick={toggleTheme}
              aria-label={`Switch to ${theme === "light" ? "dark" : "light"} theme`}
              title={`Switch to ${theme === "light" ? "dark" : "light"} theme`}
            >
              {theme === "light" ? <Moon size={17} /> : <Sun size={17} />}
            </button>
            <a href={repo} aria-label="View Retrace on GitHub">
              Star on GitHub <ArrowUpRight size={15} />
            </a>
          </div>
        </header>
        <section className="intro">
          <div className="intro-copy">
            <p className="eyebrow">
              <span className="live-dot" /> THE INTERACTIVE RECOVERY LAB
            </p>
            <h1>
              Crash the worker.
              <br />
              <span>Keep the progress.</span>
            </h1>
            <p className="intro-lede">
              See exactly what survives a process failure. Step through a real
              recovery story, inspect every checkpoint, and watch a new worker
              pick up where the last one stopped.
            </p>
            <div className="intro-actions">
              <a className="hero-primary" href="#lab">
                Explore the demo <ArrowRight size={17} />
              </a>
              <a className="hero-secondary" href="#install">
                Run it yourself <ArrowUpRight size={16} />
              </a>
            </div>
            <div className="intro-proof" aria-label="Retrace capabilities">
              <span><Check size={14} /> SQLite durability</span>
              <span><Check size={14} /> Fenced workers</span>
              <span><Check size={14} /> No services</span>
            </div>
          </div>
          <div className="hero-trace" aria-label="Illustrative recovery trace">
            <div className="hero-trace-head">
              <span><span className="trace-pulse" /> RECOVERY TRACE</span>
              <span>RUN / ORDERS-042</span>
            </div>
            <div className="hero-trace-body">
              <div className="trace-line committed">
                <span className="trace-icon"><Check size={15} /></span>
                <div><strong>Input checkpointed</strong><small>128 rows saved to SQLite</small></div>
                <span className="trace-time">00:00.48</span>
              </div>
              <div className="trace-line interrupted">
                <span className="trace-icon"><CircleStop size={15} /></span>
                <div><strong>Worker interrupted</strong><small>Uncommitted step discarded</small></div>
                <span className="trace-time">00:00.95</span>
              </div>
              <div className="trace-line recovered">
                <span className="trace-icon"><GitBranch size={15} /></span>
                <div><strong>Run recovered</strong><small>Two checkpoints reused</small></div>
                <span className="trace-time">00:16.00</span>
              </div>
            </div>
            <div className="hero-trace-foot">
              <span><ShieldCheck size={15} /> Progress preserved</span>
              <span>SIMULATED TRACE <ArrowUpRight size={13} /></span>
            </div>
          </div>
        </section>
        <section id="lab" className="lab" aria-label="Workflow recovery playground">
          <div className="lab-heading">
            <div>
              <p className="eyebrow">WORKFLOW / CSV ORDERS</p>
              <h2>
                orders-pipeline{" "}
                <span className={`badge ${run.status.toLowerCase()}`}>
                  {run.status}
                </span>
              </h2>
            </div>
            <button
              className="quiet"
              onClick={() => setShowExport(!showExport)}
              aria-expanded={showExport}
            >
              <Download size={16} /> Export sample
            </button>
          </div>
          {showExport && (
            <section className="export-panel" aria-label="Sample JSON export">
              <div className="panel-heading">
                <strong>Sample JSON snapshot</strong>
                <button
                  aria-label="Close export"
                  onClick={() => setShowExport(false)}
                >
                  Close
                </button>
              </div>
              <p>
                This synthetic snapshot describes the current event. Uncommitted
                steps have no saved output.
              </p>
              <div className="export-actions">
                <button onClick={download}>
                  <Download size={16} />
                  Download JSON
                </button>
                <button
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(
                        JSON.stringify({ simulation: true, ...run }, null, 2),
                      );
                      setExportMessage("JSON copied");
                    } catch {
                      setExportMessage("Select and copy the JSON below");
                    }
                  }}
                >
                  <Copy size={16} />
                  Copy JSON
                </button>
                <span role="status">{exportMessage}</span>
              </div>
              <pre tabIndex={0}>
                {JSON.stringify({ simulation: true, ...run }, null, 2)}
              </pre>
            </section>
          )}
          <div className="metrics">
            <div>
              <span>CHECKPOINTS</span>
              <strong>
                {run.completed}
                <small> / 4</small>
              </strong>
            </div>
            <div>
              <span>WORKER EPOCH</span>
              <strong>{run.epoch.toString().padStart(2, "0")}</strong>
            </div>
            <div>
              <span>REUSED STEPS</span>
              <strong>
                {run.reused.toString().padStart(2, "0")}
                <small> on recovery</small>
              </strong>
            </div>
            <div>
              <span>EXECUTION</span>
              <strong className="metric-text">At least once</strong>
            </div>
          </div>
          <div className="canvas">
            <div className="canvas-caption">
              <GitBranch size={15} />
              <span>DEPENDENCY GRAPH</span>
              <span className="caption-end">Select a step to inspect</span>
            </div>
            <div className="graph">
              {run.tasks.map((s, i) => {
                const Icon = icons[i];
                return (
                  <div className="graph-slot" key={s.key}>
                    <button
                      className={`node ${s.status} ${selected === i ? "selected" : ""}`}
                      aria-pressed={selected === i}
                      aria-label={`${s.name}: ${s.status}`}
                      onClick={() => setSelected(i)}
                    >
                      <div className="node-top">
                        <span className="node-icon">
                          <Icon size={19} />
                        </span>
                        <span className="node-number">0{i + 1}</span>
                      </div>
                      <strong>{s.name}</strong>
                      <span className="node-status">
                        {s.status === "committed" ? (
                          <Check size={13} />
                        ) : s.status === "interrupted" ? (
                          <CircleStop size={13} />
                        ) : (
                          <span className={`status-dot ${s.status}`} />
                        )}{" "}
                        {s.status}
                      </span>
                    </button>
                    {i < 3 && (
                      <ArrowRight
                        className={`connector ${s.status === "committed" ? "complete" : ""}`}
                        size={20}
                      />
                    )}
                  </div>
                );
              })}
            </div>
            <div className="legend">
              <span>
                <i className="status-dot committed" /> Committed
              </span>
              <span>
                <i className="status-dot running" /> Running
              </span>
              <span>
                <i className="status-dot interrupted" /> Interrupted
              </span>
              <span>
                <i className="status-dot pending" /> Pending
              </span>
            </div>
          </div>
          <div className={`playback ${phase === 4 ? "crashed" : ""}`}>
            <div className="playback-copy">
              <span className="event-number">
                {String(phase + 1).padStart(2, "0")} / 08
              </span>
              <p aria-live="polite">{run.events.at(-1)?.text}</p>
            </div>
            <div className="controls">
              <button
                className="icon-button"
                aria-label="Restart walkthrough"
                onClick={reset}
              >
                <RotateCcw size={17} />
              </button>
              <button
                disabled={phase === 7 || playing}
                onClick={() => {
                  setPlaying(false);
                  setPhase(nextPhase);
                }}
              >
                Next event <ChevronRight size={16} />
              </button>
              <button
                className="primary"
                onClick={() => (playing ? setPlaying(false) : play())}
              >
                {playing ? <Pause size={16} /> : <Play size={16} />}{" "}
                {playing
                  ? "Pause"
                  : phase === 4
                    ? "Resume run"
                    : phase === 7
                      ? "Replay"
                      : "Play walkthrough"}
              </button>
            </div>
            <div className="phase-rail" aria-label="Recovery timeline">
              {phaseLabels.map((label, index) => (
                <button
                  key={label}
                  type="button"
                  className={index === phase ? "current" : index < phase ? "passed" : ""}
                  aria-label={`Go to event ${index + 1}: ${label}`}
                  aria-current={index === phase ? "step" : undefined}
                  title={`${index + 1}. ${label}`}
                  onClick={() => seek(index)}
                >
                  <span />
                </button>
              ))}
            </div>
          </div>
          <div className="detail-grid">
            <section className="inspector">
              <div className="panel-heading">
                <span className="eyebrow">STEP INSPECTOR</span>
                <span className="mono">{task.key}</span>
              </div>
              <h3>{task.name}</h3>
              <p className="description">{task.description}</p>
              <div className="attempt-info">
                <span>
                  {task.attempts} attempt{task.attempts !== 1 ? "s" : ""}
                </span>
                <span>
                  {phase >= 5 && selected < 2
                    ? "Checkpoint reused"
                    : "Worker-owned execution"}
                </span>
              </div>
              <Tabs defaultValue="output">
                <TabsList className="detail-tabs">
                  <TabsTrigger value="output">Output</TabsTrigger>
                  <TabsTrigger value="attempts">Attempts</TabsTrigger>
                </TabsList>
                <TabsContent value="output">
                  <pre>
                    {task.status === "committed"
                      ? JSON.stringify(task.output, null, 2)
                      : "// No committed output yet.\n// Only successful results become checkpoints."}
                  </pre>
                </TabsContent>
                <TabsContent value="attempts">
                  <div className="attempt-list">
                    {task.attempts === 0 ? (
                      <p>No attempts yet.</p>
                    ) : (
                      Array.from({ length: task.attempts }, (_, n) => (
                        <div key={n}>
                          <span>Attempt {n + 1}</span>
                          <span className="mono">
                            {selected === 2 && n === 0 && phase >= 4
                              ? "interrupted"
                              : n === task.attempts - 1
                                ? task.status
                                : "committed"}
                          </span>
                        </div>
                      ))
                    )}
                  </div>
                </TabsContent>
              </Tabs>
            </section>
            <section className="journal">
              <div className="panel-heading">
                <span className="eyebrow">EVENT JOURNAL</span>
                <button
                  className={`filter-button ${onlySelected ? "on" : ""}`}
                  aria-pressed={onlySelected}
                  onClick={() => setOnlySelected(!onlySelected)}
                >
                  {onlySelected ? "Selected step" : "All steps"}
                </button>
              </div>
              <label className="journal-search">
                <Search size={15} aria-hidden="true" />
                <span className="sr-only">Search events</span>
                <input
                  type="search"
                  value={journalQuery}
                  onChange={(event) => setJournalQuery(event.target.value)}
                  placeholder="Search events"
                />
              </label>
              <div className="journal-list">
                {journal.length ? (
                  journal
                    .slice()
                    .reverse()
                    .map((e) => (
                      <div
                        className={`journal-event ${e.kind.includes("interrupted") ? "warn" : ""}`}
                        key={e.kind + e.time}
                      >
                        <time>{e.time}</time>
                        <div>
                          <strong>{e.kind}</strong>
                          <p>{e.text}</p>
                        </div>
                      </div>
                    ))
                ) : (
                  <p className="empty">
                    {journalQuery.trim()
                      ? "No events match your search."
                      : "No events for this step yet."}
                  </p>
                )}
              </div>
              <p className="journal-foot">
                Sample timestamps · {journal.length} events
              </p>
            </section>
          </div>
        </section>
        <p className="disclosure">
          <span className="sample-pill">SIMULATION</span> Synthetic data in your
          browser. No Python runs, file uploads, or persisted data. Autoplay
          pauses at the crash so you can inspect the checkpoints.
        </p>
        <SignalLab />
        <section className="ownership" aria-label="Worker ownership model">
          <div className="ownership-head">
            <div>
              <p className="eyebrow">MULTI-PROCESS EXECUTION</p>
              <h2>One run. One owner. A recoverable handoff.</h2>
              <p>
                Local worker processes compete for queued runs through one SQLite
                transaction. Each claimed run has an epoch; a previous owner
                cannot commit after takeover.
              </p>
            </div>
            <a href={`${docsBase}/architecture/`}>
              Read the protocol <ArrowRight size={15} />
            </a>
          </div>
          <div className="ownership-map">
            <div className={`ownership-card ${phase === 4 ? "lost" : phase >= 5 ? "fenced" : "owns"}`}>
              <span className="ownership-icon"><Server size={19} /></span>
              <span className="ownership-label">PROCESS 01</span>
              <strong>{phase === 4 ? "Lease expired" : phase >= 5 ? "Fenced out" : "Original owner"}</strong>
              <small>epoch 01 · {phase === 4 ? "crashed" : phase >= 5 ? "stale" : "active"}</small>
            </div>
            <div className="ownership-link" aria-hidden="true"><span /></div>
            <div className="ownership-card ledger">
              <span className="ownership-icon"><Database size={19} /></span>
              <span className="ownership-label">LOCAL SQLITE WAL</span>
              <strong>{run.completed} / 4 checkpoints</strong>
              <small>atomic claim · durable journal</small>
            </div>
            <div className="ownership-link" aria-hidden="true"><span /></div>
            <div className={`ownership-card ${phase >= 5 ? "owns" : "waiting"}`}>
              <span className="ownership-icon"><Server size={19} /></span>
              <span className="ownership-label">PROCESS 02</span>
              <strong>{phase >= 5 ? "Recovered owner" : "Waiting to claim"}</strong>
              <small>epoch 02 · {phase >= 5 ? "active" : "standby"}</small>
            </div>
          </div>
          <div className="queue-features">
            <div>
              <ShieldCheck size={18} />
              <span><strong>Retry-safe submission</strong><small>The same key and input resolve to one run ID, even when producers race.</small></span>
            </div>
            <div>
              <Clock3 size={18} />
              <span><strong>Durable signals</strong><small>Wait for a decision without occupying a worker; resume when its signal arrives.</small></span>
            </div>
            <div>
              <CircleStop size={18} />
              <span><strong>Fenced cancellation</strong><small>Stop queued or active runs; preserve checkpoints and reject stale worker writes.</small></span>
            </div>
            <div>
              <Pause size={18} />
              <span><strong>Graceful worker drain</strong><small>Stop new claims and hand unfinished runs to another local worker without waiting for a lease to expire.</small></span>
            </div>
          </div>
          <div className="ownership-foot">
            <LockKeyhole size={15} />
            <span>Fencing protects Retrace checkpoints. External side effects still need idempotency keys.</span>
          </div>
        </section>
        <section id="install" className="install">
          <div>
            <p className="eyebrow">YOUR PIPELINE, NEXT</p>
            <h2>
              From playground
              <br />
              to your terminal.
            </h2>
            <p>Python 3.11+ · One SQLite file · Zero runtime dependencies</p>
            <a href={`${docsBase}/getting-started/`}>
              Five-minute recovery walkthrough <ArrowRight size={16} />
            </a>
          </div>
          <div className="terminal">
            <div className="terminal-heading">
              <span>
                <Terminal size={15} /> QUICKSTART
              </span>
              <button onClick={copy}>
                <Copy size={14} />
                {copied || "Copy install"}
              </button>
            </div>
            <code>
              <span>$ </span>
              {command}
              <br />
              <span>$ </span>retrace demo
              <br />
              <span>$ </span>retrace serve
            </code>
          </div>
        </section>
        <footer>
          <span>Retrace · Early alpha · MIT</span>
          <a href={`${repo}/issues/new/choose`}>
            Tell us where you got stuck <ArrowUpRight size={14} />
          </a>
        </footer>
      </div>
    </main>
  );
}
