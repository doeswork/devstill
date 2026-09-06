import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { Type } from "typebox";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

type PlanLabel = "Read" | "Add" | "Remove" | "Modify" | "Delete" | "New";

const workspace = fs.realpathSync(process.env.DEVSTILL_PI_WORKSPACE ?? process.cwd());
const maximum = Number.parseInt(process.env.DEVSTILL_PI_MAX_TOOL_CALLS ?? "12", 10);
const labels = new Map<string, PlanLabel>(
  Object.entries(
    JSON.parse(process.env.DEVSTILL_PI_LABELS ?? "{}") as Record<string, PlanLabel>,
  ).map(([target, label]) => [path.resolve(workspace, target), label]),
);
const originals = new Map<string, string>(
  Object.entries(
    JSON.parse(process.env.DEVSTILL_PI_ORIGINAL_HASHES ?? "{}") as Record<string, string>,
  ).map(([target, digest]) => [path.resolve(workspace, target), digest]),
);
const maximumReadChars = Number.parseInt(
  process.env.DEVSTILL_PI_MAX_READ_CHARS ?? "12000",
  10,
);
const reasoning = process.env.DEVSTILL_PI_REASONING ?? "off";
let calls = 0;
let readChars = 0;
let completed = false;

function candidatePath(input: string): string {
  return path.resolve(workspace, input);
}

function resolvedInputPath(input: unknown): string | undefined {
  if (!input || typeof input !== "object") return undefined;
  const value = (input as { path?: unknown }).path;
  if (typeof value !== "string") return undefined;
  return candidatePath(value);
}

function insideWorkspace(candidate: string): boolean {
  return candidate === workspace || candidate.startsWith(`${workspace}${path.sep}`);
}

function safeRegularFile(candidate: string): boolean {
  try {
    return fs.lstatSync(candidate).isFile();
  } catch {
    return false;
  }
}

function digest(candidate: string): string | undefined {
  if (!safeRegularFile(candidate)) return undefined;
  return crypto.createHash("sha256").update(fs.readFileSync(candidate)).digest("hex");
}

function displayPath(candidate: string): string {
  return path.relative(workspace, candidate).split(path.sep).join("/");
}

function unmetContracts(): string[] {
  const unmet: string[] = [];
  for (const [candidate, label] of labels) {
    const target = displayPath(candidate);
    if (label === "Read") {
      if (!safeRegularFile(candidate) || digest(candidate) !== originals.get(candidate)) {
        unmet.push(`${target}: Read target changed`);
      }
      continue;
    }
    if (label === "Delete") {
      if (fs.existsSync(candidate)) unmet.push(`${target}: Delete target still exists`);
      continue;
    }
    if (label === "New") {
      if (!safeRegularFile(candidate)) unmet.push(`${target}: New target is not a file`);
      continue;
    }
    if (!safeRegularFile(candidate)) {
      unmet.push(`${target}: ${label} target must remain a file`);
    } else if (digest(candidate) === originals.get(candidate)) {
      unmet.push(`${target}: ${label} target is unchanged`);
    }
  }
  return unmet;
}

export default function (pi: ExtensionAPI) {
  pi.on("before_agent_start", async () => ({
    systemPrompt: [
      "You are Devstill's strict Plan executor.",
      "The Plan is approved. Do not investigate, redesign, explain, or propose alternatives.",
      "Use only active scoped tools and only exact paths named in the Plan.",
      "Read only when required, make the smallest complete edits, and call complete_plan last.",
      `You have ${maximum} read/edit tool calls; complete_plan does not consume this budget.`,
      "If complete_plan reports unmet contracts, fix those files before checking again. Repeating the check does not approve them.",
      "Every response must call exactly one tool. Never return prose as the final action.",
    ].join("\n"),
  }));

  pi.on("before_provider_request", (event) => {
    const payload = { ...(event.payload as Record<string, unknown>), tool_choice: "required" };
    if (reasoning === "off") {
      const existing = payload.chat_template_kwargs;
      payload.chat_template_kwargs = {
        ...(existing && typeof existing === "object" ? existing : {}),
        enable_thinking: false,
      };
    }
    return payload;
  });

  pi.registerTool({
    name: "read",
    label: "Read approved file",
    description: "Read at most 240 lines from one exact approved existing Plan file.",
    promptSnippet: "Read a bounded section of an exact approved Plan file",
    parameters: Type.Object({
      path: Type.String(),
      offset: Type.Optional(Type.Integer({ minimum: 1 })),
      limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 240 })),
    }),
    async execute(_id, params) {
      const candidate = candidatePath(params.path);
      const label = labels.get(candidate);
      if (!insideWorkspace(candidate) || !label || label === "New") {
        return {
          content: [{ type: "text", text: "Blocked: path is not an approved existing Plan file." }],
          details: { blocked: true },
        };
      }
      if (!safeRegularFile(candidate)) {
        return {
          content: [{ type: "text", text: "Blocked: approved path is not a regular file." }],
          details: { blocked: true },
        };
      }
      const remaining = Math.max(0, maximumReadChars - readChars);
      if (remaining === 0) {
        return {
          content: [{ type: "text", text: "Blocked: Devstill's cumulative read budget is exhausted." }],
          details: { blocked: true },
        };
      }
      const lines = fs.readFileSync(candidate, "utf8").split(/\r?\n/);
      const offset = params.offset ?? 1;
      const limit = Math.min(params.limit ?? 240, 240);
      let rendered = lines.slice(offset - 1, offset - 1 + limit).join("\n");
      const allowance = Math.min(12000, remaining);
      if (rendered.length > allowance) rendered = `${rendered.slice(0, allowance)}\n… truncated`;
      readChars += Math.min(rendered.length, allowance);
      return {
        content: [{ type: "text", text: rendered }],
        details: { path: displayPath(candidate), offset, lines: limit, readChars },
      };
    },
  });

  pi.registerTool({
    name: "devstill_delete",
    label: "Delete approved file",
    description: "Delete one exact existing file labeled Delete in the approved Plan.",
    promptSnippet: "Delete an exact Plan target labeled Delete",
    parameters: Type.Object({ path: Type.String() }),
    async execute(_id, params) {
      const candidate = candidatePath(params.path);
      if (!insideWorkspace(candidate) || labels.get(candidate) !== "Delete") {
        return {
          content: [{ type: "text", text: "Blocked: only an exact Delete target may be removed." }],
          details: { blocked: true },
        };
      }
      if (!safeRegularFile(candidate)) {
        return {
          content: [{ type: "text", text: "Blocked: Delete target is not a regular file." }],
          details: { blocked: true },
        };
      }
      fs.unlinkSync(candidate);
      return {
        content: [{ type: "text", text: `Deleted ${displayPath(candidate)}` }],
        details: { path: displayPath(candidate) },
      };
    },
  });

  pi.registerTool({
    name: "complete_plan",
    label: "Check staged changes",
    description: "Verify every approved Plan file contract and finish only when all pass.",
    promptSnippet: "Verify all Plan contracts and terminate a successful execution",
    parameters: Type.Object({}),
    async execute() {
      const unmet = unmetContracts();
      if (unmet.length) {
        return {
          content: [{ type: "text", text: `Plan is incomplete:\n${unmet.join("\n")}` }],
          details: { unmet },
        };
      }
      completed = true;
      return {
        content: [{ type: "text", text: "Every approved Plan contract is satisfied." }],
        details: { unmet: [] },
        terminate: true,
      };
    },
  });

  pi.on("tool_call", async (event) => {
    if (completed) {
      return { block: true, reason: "The Plan is already complete; no further tools may run." };
    }
    // Verification does not consume a read/edit slot. The Python runner also
    // ends the run at the budget boundary and independently checks the patch.
    if (event.toolName !== "complete_plan") {
      calls += 1;
      if (calls > maximum) {
        return { block: true, reason: `Devstill tool-call limit (${maximum}) reached.` };
      }
    }
    const candidate = resolvedInputPath(event.input);
    if (candidate && (!insideWorkspace(candidate) || !labels.has(candidate))) {
      return { block: true, reason: "Devstill blocks paths outside the approved Plan." };
    }
    if (event.toolName === "edit" || event.toolName === "write") {
      if (!candidate) return { block: true, reason: "Devstill requires an exact Plan path." };
      const label = labels.get(candidate);
      const allowed =
        event.toolName === "write"
          ? label === "Add" || label === "Remove" || label === "Modify" || label === "New"
          : label === "Add" || label === "Remove" || label === "Modify";
      if (!allowed) {
        return { block: true, reason: `Devstill blocks ${event.toolName} for label ${label ?? "unknown"}.` };
      }
    }
    if (["bash", "grep", "find", "ls"].includes(event.toolName)) {
      return { block: true, reason: "Devstill strict Plan execution disables discovery and shell tools." };
    }
    return undefined;
  });
}
