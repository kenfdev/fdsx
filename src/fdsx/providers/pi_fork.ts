// Loaded only by the dedicated, prompt-free Pi preparation subprocess.
// SessionManager owns all history selection and persistence. No replay/copying
// of messages is implemented here. The final CLI uses the returned child path.
import { SessionManager } from "@earendil-works/pi-coding-agent";
import { writeSync } from "node:fs";

export default function () {
  try {
    const request = JSON.parse(process.env.FDSX_PI_FORK_REQUEST ?? "null");
    if (!request || typeof request.path !== "string" ||
        typeof request.endpoint !== "string" || typeof request.directory !== "string") {
      throw new Error("Invalid fork request");
    }
    const source = SessionManager.open(request.path);
    if (source.getSessionId() !== request.id || !source.getEntry(request.endpoint)) {
      throw new Error("Missing source endpoint");
    }
    // forkFrom gives the child the current working directory. Branch selection
    // then pins the child to the completed endpoint, even after later appends.
    const child = SessionManager.forkFrom(request.path, process.cwd(), request.directory);
    child.createBranchedSession(request.endpoint);
    const path = child.getSessionFile();
    const branch = child.getBranch().filter(entry => entry.type !== "label");
    if (!path || path === request.path || branch.at(-1)?.id !== request.endpoint) {
      throw new Error("Native fork did not select the requested endpoint");
    }
    writeSync(1, JSON.stringify({ path }) + "\n");
    process.exit(0);
  } catch {
    // Never print native errors, which can contain conversation content.
    writeSync(2, "Native Pi endpoint fork failed\n");
    process.exit(1);
  }
}
