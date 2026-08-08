import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { dirname, extname, normalize, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:http";

const dashboardRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = resolve(dashboardRoot, "..");
const outputRoot = resolve(dashboardRoot, "dist");
const generatedDashboard = resolve(repositoryRoot, "data", "benchmark_runs", "dashboard.json");
const port = Number.parseInt(process.env.SPOTLIGHT_DASHBOARD_PORT ?? "4173", 10);

const contentTypes = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".png", "image/png"],
]);

const server = createServer(async (request, response) => {
  try {
    const requestUrl = new URL(request.url ?? "/", "http://127.0.0.1");
    if (requestUrl.pathname === "/") {
      response.writeHead(302, { Location: "/dashboard/dist/" });
      response.end();
      return;
    }

    let pathname = decodeURIComponent(requestUrl.pathname);
    let requestedPath;
    if (pathname === "/data/benchmark_runs/dashboard.json") {
      requestedPath = generatedDashboard;
    } else if (pathname.startsWith("/dashboard/dist/")) {
      let relativePath = pathname.slice("/dashboard/dist/".length);
      if (!relativePath || relativePath.endsWith("/")) relativePath += "index.html";
      requestedPath = resolve(outputRoot, normalize(relativePath));
      if (
        requestedPath !== outputRoot &&
        !requestedPath.startsWith(`${outputRoot}${sep}`)
      ) {
        response.writeHead(403).end("Forbidden");
        return;
      }
    } else {
      response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      response.end("Not found");
      return;
    }

    const details = await stat(requestedPath);
    if (!details.isFile()) throw new Error("Not a file");
    response.writeHead(200, {
      "Cache-Control": "no-store",
      "Content-Type": contentTypes.get(extname(requestedPath)) ?? "application/octet-stream",
    });
    createReadStream(requestedPath).pipe(response);
  } catch {
    response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    response.end("Not found");
  }
});

server.listen(port, "127.0.0.1", () => {
  console.log(`Spotlight dashboard: http://127.0.0.1:${port}/dashboard/dist/`);
  console.log("Press Ctrl+C to stop the local server.");
});
