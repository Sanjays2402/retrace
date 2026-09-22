# Launch kit — drafts, not posted

## Short introduction

I built Retrace for local Python pipelines that should survive a process crash without
repeating completed steps. It uses one SQLite file, has no runtime dependencies, and
includes a read-only inspector for checkpoints, retries, and recovery history.

It is early alpha: one worker owns a run, task execution is at least once, and external
side effects need downstream idempotency. It is not a distributed workflow service.

Try the five-minute recovery walkthrough:
https://github.com/Sanjays2402/retrace/blob/main/docs/getting-started.md

I would especially value feedback from someone trying it on a small CSV import or batch
API job. Where did setup or recovery behavior surprise you?

## Two-minute walkthrough script

1. Show a four-step workflow and explain that committed outputs survive restarts.
2. Run the crash demo; point out the intentional exit and the saved run ID.
3. Resume after lease expiry. Select a completed step: its attempt count stays one.
4. Open the attempt timeline to show interrupted and retried work.
5. Show the HTTP example: two requests, one accepted delivery.
6. Explain the local-disk and at-least-once boundaries, then link to the quickstart.

## First-user feedback

Ask volunteers what they tried, how long the first successful run took, where they got
stuck, and whether recovery behaved as expected. Track reports in GitHub issues. Do not
collect credentials or private datasets. Measure successful real workflows and actionable
feedback before treating stars or download counts as adoption.
