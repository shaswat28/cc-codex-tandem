// Offline companion double. Configuration and state belong to each test.
import { mkdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve } from "node:path";

const valueOptions = new Set([
  "cwd", "model", "effort", "prompt-file", "job-id", "base", "scope",
  "timeout-ms", "poll-interval-ms", "source",
]);
const [command, ...args] = process.argv.slice(2);
const options = {};
const positionals = [];
for (let index = 0; index < args.length; index++) {
  const arg = args[index];
  if (arg.startsWith("--")) {
    const name = arg.slice(2);
    options[name] = valueOptions.has(name) ? args[++index] : true;
  } else {
    positionals.push(arg);
  }
}

function emit(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

function readState(path) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
    return { attempt: 0, jobs: [] };
  }
}

// Reject escapes, including existing symlinks, before making any edit.
function editFiles(cwd, files) {
  const root = realpathSync(cwd);
  const edits = Object.entries(files).map(([name, content]) => {
    const target = resolve(root, name);
    const inside = (path) => {
      const rel = relative(root, path);
      return rel !== ".." && !rel.startsWith("../") && !isAbsolute(rel);
    };
    if (isAbsolute(name) || !inside(target) || target === root) {
      throw new Error(`Edit escapes working directory: ${name}`);
    }
    let ancestor = target;
    while (true) {
      try {
        if (!inside(realpathSync(ancestor))) throw new Error(`Edit escapes working directory: ${name}`);
        break;
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
        ancestor = dirname(ancestor);
      }
    }
    if (typeof content !== "string") throw new Error("File contents must be strings");
    return [target, content];
  });
  for (const [target, content] of edits) {
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, content);
  }
}

async function main() {
  const raw = process.env.CC_TANDEM_FAKE_SCENARIO_FILE
    ? readFileSync(process.env.CC_TANDEM_FAKE_SCENARIO_FILE, "utf8")
    : process.env.CC_TANDEM_FAKE_SCENARIO || '"succeed"';
  const config = JSON.parse(raw);
  const statePath = process.env.CC_TANDEM_FAKE_STATE;
  if (!statePath) throw new Error("CC_TANDEM_FAKE_STATE must name a test-owned state file");
  const state = readState(statePath);
  const save = () => writeFileSync(statePath, JSON.stringify(state));
  const base = typeof config === "string" ? { scenario: config } : config;

  if (["status", "result", "cancel"].includes(command)) {
    if (base.scenario === "invalid_json") return process.stdout.write("{invalid JSON\n");
    if (base.scenario === "exit_nonzero") {
      process.stderr.write("Fake process error\n");
      process.exitCode = 7;
      return;
    }
    const id = positionals[0];
    if (command === "status" && !id) {
      emit(base.status ?? {
        running: state.jobs.filter((job) => job.status === "running"),
        recent: state.jobs.filter((job) => job.status !== "running"),
      });
      return;
    }
    const job = id ? state.jobs.find((item) => item.id === id) : state.jobs.at(-1);
    if (!job) throw new Error("Unknown fake job");
    if (command === "cancel") {
      job.status = "cancelled";
      save();
      emit({ jobId: job.id, status: job.status });
    } else {
      emit(command === "result" ? { job, storedJob: job } : { job });
    }
    return;
  }
  if (command !== "task") throw new Error(`Unsupported fake command: ${command}`);
  const entry = base.attempts?.[Math.min(state.attempt, base.attempts.length - 1)] ?? base;
  const scenario = typeof entry === "string" ? entry : entry.scenario ?? "succeed";
  const settings = typeof entry === "string" ? base : { ...base, ...entry };
  const known = ["succeed", "fail", "usage_limit", "hang", "exit_nonzero", "invalid_json", "no_change", "awaiting_input"];
  if (!known.includes(scenario)) throw new Error(`Unknown fake scenario: ${scenario}`);
  state.attempt++;
  const job = {
    id: `fake-job-${state.attempt}`,
    status: scenario === "hang" ? "running" : ["fail", "usage_limit", "exit_nonzero"].includes(scenario) ? "failed" : "completed",
    summary: scenario === "usage_limit" ? "You've hit your usage limit" : `Fake ${scenario}`,
  };
  if (scenario === "awaiting_input") {
    job.result = { rawOutput: settings.question ?? "Should I retry the setup or stop here?\n1. Retry\n2. Stop" };
  }
  state.jobs.push(job);
  save();
  if (scenario === "succeed") {
    try {
      editFiles(options.cwd ?? process.cwd(), settings.files ?? {});
    } catch (error) {
      job.status = "failed";
      job.summary = error.message;
      save();
      throw error;
    }
  }
  if (options.background) {
    emit({ jobId: job.id, status: "queued", title: "Fake task", summary: job.summary });
    return;
  }
  if (scenario === "hang") {
    process.stdout.write("Fake task running\n");
    await new Promise(() => { setInterval(() => {}, 1000); });
  } else if (scenario === "invalid_json") {
    process.stdout.write("{invalid JSON\n");
  } else if (scenario === "exit_nonzero") {
    process.stderr.write("Fake process error\n");
    process.exitCode = 7;
  } else {
    emit({ jobId: job.id, status: job.status, summary: job.summary, ...job.result });
    process.exitCode = job.status === "failed" ? 1 : 0;
  }
}

main().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 2;
});
