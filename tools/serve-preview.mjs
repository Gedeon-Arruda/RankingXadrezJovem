import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, "..");
const docsDir = path.join(projectRoot, "docs");
const port = 51300;

const mimeTypes = new Map([
  [".html", "text/html; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".js", "application/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".svg", "image/svg+xml"],
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".ico", "image/x-icon"]
]);

function resolveFilePath(requestUrl) {
  const pathname = decodeURIComponent(new URL(requestUrl, `http://127.0.0.1:${port}`).pathname);
  const relativePath = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
  const resolvedPath = path.normalize(path.join(docsDir, relativePath));

  if (!resolvedPath.startsWith(docsDir)) {
    return null;
  }

  return resolvedPath;
}

const server = createServer(async (request, response) => {
  const filePath = resolveFilePath(request.url || "/");

  if (!filePath) {
    response.statusCode = 403;
    response.end("Forbidden");
    return;
  }

  try {
    const body = await readFile(filePath);
    const extension = path.extname(filePath).toLowerCase();
    response.setHeader("Content-Type", mimeTypes.get(extension) || "application/octet-stream");
    response.end(body);
  } catch {
    response.statusCode = 404;
    response.end("Not found");
  }
});

server.listen(port, "127.0.0.1", () => {
  console.log(`Preview em http://127.0.0.1:${port}`);
});
