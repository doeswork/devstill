import fs from "node:fs";
import path from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const workspace = fs.realpathSync(process.env.DEVSTILL_PI_WORKSPACE ?? process.cwd());
const maximum = Number.parseInt(process.env.DEVSTILL_PI_MAX_TOOL_CALLS ?? "16", 10);
const writable = new Set(
  JSON.parse(process.env.DEVSTILL_PI_WRITABLE_PATHS ?? "[]").map((value: string) =>
    path.resolve(workspace, value),
  ),
);
let calls = 0;

function resolvedInputPath(input: unknown): string | undefined {
  if (!input || typeof input !== "object") return undefined;
  const value = (input as { path?: unknown }).path;
  if (typeof value !== "string") return undefined;
  return path.resolve(workspace, value);
}

function insideWorkspace(candidate: string): boolean {
  return candidate === workspace || candidate.startsWith(`${workspace}${path.sep}`);
}

function insideGitMetadata(candidate: string): boolean {
  const relative = path.relative(workspace, candidate);
  return relative === ".git" || relative.startsWith(`.git${path.sep}`);
}

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event) => {
    calls += 1;
    if (calls > maximum) {
      return { block: true, reason: `Devstill tool-call limit (${maximum}) reached.` };
    }
    const candidate = resolvedInputPath(event.input);
    if (candidate && !insideWorkspace(candidate)) {
      return { block: true, reason: "Devstill blocks access outside the staging workspace." };
    }
    if (candidate && insideGitMetadata(candidate)) {
      return { block: true, reason: "Devstill blocks access to staging Git metadata." };
    }
    if (event.toolName === "edit" || event.toolName === "write") {
      if (!candidate || !writable.has(candidate)) {
        return { block: true, reason: "Devstill blocks writes outside approved plan targets." };
      }
    }
    if (event.toolName === "bash") {
      return { block: true, reason: "Devstill execution does not permit model-selected shell commands." };
    }
    return undefined;
  });
}
