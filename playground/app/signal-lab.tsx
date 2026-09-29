"use client";

import { useState } from "react";
import { ArrowRight, Check, Clock3, Database, RotateCcw, Send, Server } from "lucide-react";
import {
  deliverSignal,
  initialSignalRun,
  resumeSignalRun,
  startSignalRun,
} from "@/lib/signals";

const repo = "https://github.com/Sanjays2402/retrace";

export function SignalLab() {
  const [run, setRun] = useState(initialSignalRun);
  const { stage, delivered } = run;
  const waiting = stage === "waiting";
  const complete = stage === "succeeded";
  const status = {
    queued: "Ready to run",
    waiting: "Waiting for approval",
    pending: "Ready for a worker",
    succeeded: "Workflow complete",
  }[stage];

  return (
    <section id="signals" className="signal-lab" aria-labelledby="signal-title">
      <div className="signal-header">
        <div>
          <p className="eyebrow">INTERACTIVE LAB / DURABLE SIGNALS</p>
          <h2 id="signal-title">A pause that survives the process.</h2>
          <p>
            Start a release workflow, send its approval, then bring a worker back.
            Try sending the approval before starting the run, too.
          </p>
        </div>
        <a href={`${repo}/blob/main/docs/api.md#durable-signals`}>
          See the API <ArrowRight size={15} />
        </a>
      </div>
      <div className="signal-layout">
        <div className="signal-flow" aria-label="Signal-gated workflow">
          <div className="signal-flow-top">
            <span>RELEASE / 042</span>
            <span className={`signal-state ${stage}`} role="status" aria-live="polite">
              <span /> {status}
            </span>
          </div>
          <div className="signal-path">
            <div className={`signal-step ${stage !== "queued" ? "done" : ""}`}>
              <span className="signal-step-icon"><Database size={18} /></span>
              <span><small>01 / CHECKPOINT</small><strong>Prepare release</strong></span>
              {stage !== "queued" && <Check size={16} aria-label="Complete" />}
            </div>
            <div className="signal-connector" aria-hidden="true" />
            <div className={`signal-step ${waiting ? "active" : delivered ? "done" : ""}`}>
              <span className="signal-step-icon"><Clock3 size={18} /></span>
              <span><small>02 / SIGNAL GATE</small><strong>Await approval</strong></span>
              {delivered && <Check size={16} aria-label="Delivered" />}
            </div>
            <div className="signal-connector" aria-hidden="true" />
            <div className={`signal-step ${complete ? "done" : ""}`}>
              <span className="signal-step-icon"><Server size={18} /></span>
              <span><small>03 / WORKER</small><strong>Publish release</strong></span>
              {complete && <Check size={16} aria-label="Complete" />}
            </div>
          </div>
          <div className="signal-worker">
            <span className={`signal-worker-light ${waiting ? "idle" : ""}`} />
            {waiting
              ? "No worker is held. No attempt or timeout budget is spent."
              : stage === "pending"
                ? "Approval persisted. A worker can now claim this run."
                : complete
                  ? "The approval payload was delivered to the task."
                  : "The signal can arrive before the gate is reached."}
          </div>
        </div>
        <div className="signal-console">
          <div className="signal-console-head"><span>CONTROL PANEL</span><span>SIMULATION</span></div>
          <p>One durable <code>approval</code> signal, one release run.</p>
          <div className="signal-inbox">
            <span>Signal inbox</span>
            <strong>{delivered ? "approval · stored" : "Empty"}</strong>
          </div>
          <div className="signal-actions">
            <button type="button" onClick={() => setRun(startSignalRun)} disabled={stage !== "queued"}>
              <ArrowRight size={16} /> Start run
            </button>
            <button type="button" onClick={() => setRun(deliverSignal)} disabled={delivered || complete}>
              <Send size={16} /> Send approval
            </button>
            <button type="button" onClick={() => setRun(resumeSignalRun)} disabled={stage !== "pending"}>
              <Server size={16} /> Resume worker
            </button>
          </div>
          <button className="signal-reset" type="button" onClick={() => setRun(initialSignalRun)}>
            <RotateCcw size={14} /> Reset scenario
          </button>
        </div>
      </div>
      <p className="signal-footnote">Browser simulation of Retrace’s signal contract · <code>Task(wait_for="approval")</code></p>
    </section>
  );
}
