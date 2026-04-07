import path from "node:path";
import { fileURLToPath } from "node:url";
import { generateChessComData } from "./lib/chesscom-source.mjs";
import { generateLichessData } from "./lib/lichess-source.mjs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, "..");
const docsDir = path.join(projectRoot, "docs");

function parseArgs(argv) {
  const args = {
    source: "all",
    stdout: false
  };

  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === "--stdout") {
      args.stdout = true;
      continue;
    }

    if (token === "--source") {
      args.source = argv[index + 1] || args.source;
      index += 1;
    }
  }

  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const writeFile = !args.stdout;

  if (args.source === "all") {
    const [lichess, chesscom] = await Promise.all([
      generateLichessData({ docsDir, writeFile }),
      generateChessComData({ docsDir, writeFile })
    ]);

    if (args.stdout) {
      process.stdout.write(JSON.stringify({ lichess, chesscom }, null, 2) + "\n");
    }
    return;
  }

  if (args.source === "lichess") {
    const payload = await generateLichessData({ docsDir, writeFile });
    if (args.stdout) {
      process.stdout.write(JSON.stringify(payload, null, 2) + "\n");
    }
    return;
  }

  if (args.source === "chesscom") {
    const payload = await generateChessComData({ docsDir, writeFile });
    if (args.stdout) {
      process.stdout.write(JSON.stringify(payload, null, 2) + "\n");
    }
    return;
  }

  throw new Error(`Fonte inválida: ${args.source}`);
}

main().catch((error) => {
  console.error(`Erro gerando dados: ${error.message}`);
  process.exitCode = 1;
});
