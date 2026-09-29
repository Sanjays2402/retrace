export type SignalStage = "queued" | "waiting" | "pending" | "succeeded";

export type SignalRun = {
  stage: SignalStage;
  delivered: boolean;
};

export const initialSignalRun: SignalRun = { stage: "queued", delivered: false };

export function startSignalRun(run: SignalRun): SignalRun {
  if (run.stage !== "queued") return run;
  return { ...run, stage: run.delivered ? "succeeded" : "waiting" };
}

export function deliverSignal(run: SignalRun): SignalRun {
  if (run.delivered || run.stage === "succeeded") return run;
  return {
    delivered: true,
    stage: run.stage === "waiting" ? "pending" : run.stage,
  };
}

export function resumeSignalRun(run: SignalRun): SignalRun {
  if (run.stage !== "pending") return run;
  return { ...run, stage: "succeeded" };
}
