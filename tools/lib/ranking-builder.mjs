import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import {
  activeSinceDays,
  dedupePlayers,
  initPlayerMetadata,
  ratingStatus,
  safeInt
} from "./shared.mjs";

async function readPreviousPlayers(outputPath) {
  try {
    const text = await readFile(outputPath, "utf8");
    const payload = JSON.parse(text);
    return Array.isArray(payload?.players) ? payload.players : [];
  } catch {
    return [];
  }
}

function buildPreviousMaps(previousPlayers) {
  const previousByUsername = new Map();
  const previousRankByUsername = new Map();

  previousPlayers.forEach((player, index) => {
    const username = String(player?.username || "").trim().toLowerCase();
    if (!username) {
      return;
    }

    previousByUsername.set(username, player);
    previousRankByUsername.set(username, index + 1);
  });

  return { previousByUsername, previousRankByUsername };
}

function enrichWithDeltasAndPositions(players, previousPlayers) {
  const { previousByUsername, previousRankByUsername } = buildPreviousMaps(previousPlayers);

  for (const player of players) {
    initPlayerMetadata(player);

    if (!String(player?.name || "").trim()) {
      player.name = "Sem nome registrado";
    }

    const username = String(player?.username || "").trim().toLowerCase();
    const previous = previousByUsername.get(username);

    if (previous) {
      player.blitz_diff = safeInt(player.blitz) - safeInt(previous.blitz);
      player.bullet_diff = safeInt(player.bullet) - safeInt(previous.bullet);
      player.rapid_diff = safeInt(player.rapid) - safeInt(previous.rapid);
    } else {
      player.blitz_diff = safeInt(player.recent_blitz_diff);
      player.bullet_diff = safeInt(player.recent_bullet_diff);
      player.rapid_diff = safeInt(player.recent_rapid_diff);
    }

    delete player.recent_blitz_diff;
    delete player.recent_bullet_diff;
    delete player.recent_rapid_diff;
  }

  players.forEach((player, index) => {
    const currentPosition = index + 1;
    const username = String(player?.username || "").trim().toLowerCase();
    const previousPosition = previousRankByUsername.get(username);

    player.position = currentPosition;

    if (!previousPosition) {
      player.position_change = null;
      player.position_arrow = null;
    } else {
      const change = previousPosition - currentPosition;
      player.position_change = change;
      player.position_arrow = change > 0 ? "\u25B2" : change < 0 ? "\u25BC" : "\u2192";
    }

    player.blitz_status = ratingStatus(player.blitz_diff);
    player.bullet_status = ratingStatus(player.bullet_diff);
    player.rapid_status = ratingStatus(player.rapid_diff);
  });
}

async function writeOutput(players, outputPath, shouldWriteFile) {
  const payload = {
    generated_at: Date.now(),
    count: players.length,
    players
  };

  if (shouldWriteFile) {
    await mkdir(path.dirname(outputPath), { recursive: true });
    await writeFile(outputPath, JSON.stringify(payload, null, 2) + "\n", "utf8");
  }

  return payload;
}

export async function finalizePlayers({
  players,
  outputPath,
  writeFile: shouldWriteFile = true,
  enrichPlayers
}) {
  const deduped = dedupePlayers(players);
  const activePlayers = deduped.filter((player) => activeSinceDays(player));
  const sortedPlayers = [...activePlayers].sort((left, right) => {
    return (
      safeInt(right.blitz) - safeInt(left.blitz) ||
      safeInt(right.bullet) - safeInt(left.bullet) ||
      safeInt(right.rapid) - safeInt(left.rapid)
    );
  });

  const previousPlayers = shouldWriteFile ? await readPreviousPlayers(outputPath) : [];
  enrichWithDeltasAndPositions(sortedPlayers, previousPlayers);

  if (typeof enrichPlayers === "function") {
    await enrichPlayers(sortedPlayers);
  }

  return writeOutput(sortedPlayers, outputPath, shouldWriteFile);
}
