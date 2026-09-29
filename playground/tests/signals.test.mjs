import test from "node:test";
import assert from "node:assert/strict";
import {
  deliverSignal,
  initialSignalRun,
  resumeSignalRun,
  startSignalRun,
} from "../lib/signals.ts";

test("waiting releases the worker until a durable signal arrives", () => {
  const waiting = startSignalRun(initialSignalRun);
  assert.deepEqual(waiting, { stage: "waiting", delivered: false });
  assert.deepEqual(resumeSignalRun(waiting), waiting);
  const ready = deliverSignal(waiting);
  assert.deepEqual(ready, { stage: "pending", delivered: true });
  assert.deepEqual(resumeSignalRun(ready), { stage: "succeeded", delivered: true });
});

test("a signal delivered before the gate is retained and skips waiting", () => {
  const early = deliverSignal(initialSignalRun);
  assert.deepEqual(early, { stage: "queued", delivered: true });
  assert.deepEqual(startSignalRun(early), { stage: "succeeded", delivered: true });
  assert.deepEqual(deliverSignal(early), early);
});
