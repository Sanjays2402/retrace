"""A workflow that releases its worker while awaiting an external decision."""

from retrace import Context, Task, Workflow


async def prepare(ctx: Context):
    return {"request": ctx.input["request"]}


async def publish(ctx: Context):
    decision = ctx.signal
    if not decision["approved"]:
        return {"published": False, "reason": decision.get("reason")}
    return {"published": True, "request": ctx.dependencies["prepare"]["request"]}


workflow = Workflow(
    "approval",
    (
        Task("prepare", prepare),
        Task("publish", publish, needs=("prepare",), wait_for="decision"),
    ),
)
