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
} from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { snapshot, nextPhase } from "@/lib/recovery";
const repo = "https://github.com/Sanjays2402/retrace";
const command =
  "python -m pip install git+https://github.com/Sanjays2402/retrace.git";
const icons = [FileText, ShieldCheck, Database, ArrowUpRight];
export default function Home() {
  const [phase, setPhase] = useState(0),
    [selected, setSelected] = useState(0),
    [playing, setPlaying] = useState(false),
    [copied, setCopied] = useState(""),
    [onlySelected, setOnlySelected] = useState(false);
  const [showExport, setShowExport] = useState(false);
  const [exportMessage, setExportMessage] = useState("");
  const run = snapshot(phase),
    task = run.tasks[selected];
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
  const journal = run.events.filter(
    (e) => !onlySelected || e.task === task.key,
  );
  return (
    <main className="shell">
      <aside className="sidebar">
        <a className="brand" href={repo}>
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
        <a
          className="nav-item"
          href={`${repo}/blob/main/docs/getting-started.md`}
        >
          <FileText size={17} /> Quickstart <ArrowUpRight size={14} />
        </a>
        <a className="nav-item" href={`${repo}/blob/main/docs/architecture.md`}>
          <Database size={17} /> Architecture <ArrowUpRight size={14} />
        </a>
        <div className="side-note">
          <span className="side-label">THE RECOVERY CONTRACT</span>
          <h2>
            The process stops.
            <br />
            The progress stays.
          </h2>
          <p>Committed steps are reused. Interrupted work runs again.</p>
          <a href={`${repo}/blob/main/docs/recovery.md`}>
            Read the guarantees <ArrowUpRight size={14} />
          </a>
        </div>
        <div className="side-footer">LOCAL-FIRST · OPEN SOURCE</div>
      </aside>
      <div className="content">
        <header className="topbar">
          <div className="breadcrumb">
            Playground <ChevronRight size={14} />
            <strong>Recovery lab</strong>
          </div>
          <a href={repo}>
            Star on GitHub <ArrowUpRight size={15} />
          </a>
        </header>
        <section className="intro">
          <div>
            <p className="eyebrow">
              <span className="live-dot" /> EXPLORE DURABLE EXECUTION
            </p>
            <h1>
              Break a run.
              <br />
              <span>Watch it recover.</span>
            </h1>
            <p>
              Take a pipeline through a crash and back.
              <br />
              Your checkpoints tell the story.
            </p>
          </div>
          <div className="intro-note">
            <span className="sample-pill">Interactive sample</span>
            <p>
              No install. No account.
              <br />A guided look at how Retrace works.
            </p>
            <a href="#install">
              Try the real engine <ArrowRight size={15} />
            </a>
          </div>
        </section>
        <section className="lab" aria-label="Workflow recovery playground">
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
                  <p className="empty">No events for this step yet.</p>
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
            <a href={`${repo}/blob/main/docs/architecture.md`}>
              Read the protocol <ArrowUpRight size={15} />
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
            <a href={`${repo}/blob/main/docs/getting-started.md`}>
              Five-minute recovery walkthrough <ArrowUpRight size={16} />
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
