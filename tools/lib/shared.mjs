export const MAX_CONCURRENCY = 8;
export const REQUEST_TIMEOUT_MS = 12_000;
export const ACTIVE_DAYS = 30;
export const LEADERBOARD_PAGE_SIZE = 50;
export const CHESSCOM_MAX_LEADERBOARD_PAGES = 512;
export const LEADERBOARD_PAGE_DELAY_MS = 220;
export const LEADERBOARD_GROUP_DELAY_MS = 2_000;
export const SPECIAL_TITLE_OVERRIDES = Object.freeze({
  gedevonarrudev: "DEV",
  gedevon_arrudev: "DEV"
});

export function safeInt(value, fallback = 0) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function safeTimestampMs(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }

  const parsed = Number.parseInt(String(value), 10);
  if (!Number.isFinite(parsed)) {
    return null;
  }

  return parsed < 1_000_000_000_000 ? parsed * 1000 : parsed;
}

export function normalizeCountryCode(value) {
  const normalized = String(value || "").trim().toUpperCase();
  return normalized || null;
}

export function extractCountryCodeFromUrl(value) {
  if (!value) {
    return null;
  }

  const code = String(value).replace(/\/+$/, "").split("/").pop();
  return normalizeCountryCode(code);
}

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function mapWithConcurrency(items, limit, worker) {
  const queue = [...items];
  const results = [];
  const workerCount = Math.max(1, Math.min(limit, queue.length || 1));

  const runners = Array.from({ length: workerCount }, async () => {
    while (queue.length > 0) {
      const item = queue.shift();
      const result = await worker(item);
      results.push(result);
    }
  });

  await Promise.all(runners);
  return results;
}

export function activeSinceDays(player, days = ACTIVE_DAYS) {
  const seenAt = safeTimestampMs(player?.seenAt);
  if (!seenAt) {
    return false;
  }

  const ageDays = (Date.now() - seenAt) / (24 * 60 * 60 * 1000);
  return ageDays <= days;
}

export function ratingStatus(diff) {
  const parsed = safeInt(diff, 0);
  if (parsed > 0) return "subiu";
  if (parsed < 0) return "caiu";
  return "manteve";
}

export function dedupePlayers(players) {
  const byUsername = new Map();

  const totalScore = (player) =>
    safeInt(player?.blitz) + safeInt(player?.bullet) + safeInt(player?.rapid);

  for (const player of players) {
    const username = String(player?.username || "").trim().toLowerCase();
    if (!username) {
      continue;
    }

    const existing = byUsername.get(username);
    if (!existing) {
      byUsername.set(username, player);
      continue;
    }

    const existingSeen = safeTimestampMs(existing.seenAt) || 0;
    const currentSeen = safeTimestampMs(player.seenAt) || 0;

    if (totalScore(player) > totalScore(existing) || currentSeen > existingSeen) {
      byUsername.set(username, player);
    }
  }

  return [...byUsername.values()];
}

export function initPlayerMetadata(player) {
  const username = String(player?.username || "").trim().toLowerCase();
  const explicitTitle = String(player?.title || "").trim().toUpperCase();
  player.title = SPECIAL_TITLE_OVERRIDES[username] || explicitTitle || null;
  player.country_code = normalizeCountryCode(player?.country_code);
  player.country_name = String(player?.country_name || "").trim() || null;

  for (const rhythm of ["blitz", "bullet", "rapid"]) {
    if (!(Object.prototype.hasOwnProperty.call(player, `${rhythm}_country_rank`))) {
      player[`${rhythm}_country_rank`] = null;
    }
  }
}

export function parseJsonLines(text) {
  return String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return line;
      }
    });
}
