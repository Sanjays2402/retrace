import test from "node:test";
import assert from "node:assert/strict";
import { snapshot, nextPhase } from "../lib/recovery.ts";

test("recovery reuses committed tasks and reruns interrupted summary", () => {
  const stopped = snapshot(4);
  assert.equal(stopped.completed, 2);
  assert.equal(stopped.tasks[2].status, "interrupted");
  const recovered = snapshot(7);
  assert.equal(recovered.completed, 4);
  assert.equal(recovered.reused, 2);
  assert.equal(recovered.epoch, 2);
  assert.deepEqual(
    recovered.tasks.map((t) => t.attempts),
    [1, 1, 2, 1],
  );
  assert.deepEqual(recovered.tasks[0].output, stopped.tasks[0].output);
  assert.equal(recovered.events.length, 8);
});
test("playback progresses one event and stops at completion", () => {
  for (let i = 0; i < 7; i++) assert.equal(nextPhase(i), i + 1);
  assert.equal(nextPhase(7), 7);
  assert.equal(snapshot(0).completed, 0);
  assert.ok(snapshot(0).tasks.every((t) => t.attempts === 0));
});
test("invalid model input is rejected without a plausible snapshot", () => {
  for (const value of [-1, 8, 0.5, NaN, Infinity, "4", null])
    assert.throws(() => snapshot(value));
});

test("snapshots only expose outputs after a checkpoint commits", () => {
  for (let phase = 0; phase <= 7; phase++) {
    for (const task of snapshot(phase).tasks) {
      assert.equal(task.output !== null, task.status === "committed");
    }
  }
});
