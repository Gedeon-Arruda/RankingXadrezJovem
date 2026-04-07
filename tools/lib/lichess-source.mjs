import path from "node:path";
import { finalizePlayers } from "./ranking-builder.mjs";
import { requestJson, requestText } from "./http-client.mjs";
import {
  MAX_CONCURRENCY,
  mapWithConcurrency,
  parseJsonLines,
  safeInt,
  safeTimestampMs
} from "./shared.mjs";

const LICHESS_TEAM_ID = "xadrezjovemes";
const LICHESS_TEAM_URL = `https://lichess.org/api/team/${encodeURIComponent(LICHESS_TEAM_ID)}/users`;
const LICHESS_USER_URL = "https://lichess.org/api/user/";

function extractRealName(profile, user) {
  const source = profile || {};
  const firstName = String(source.firstName || source.first || "").trim();
  const lastName = String(source.lastName || source.last || "").trim();

  if (firstName && lastName) {
    return `${firstName} ${lastName}`.trim();
  }

  return String(
    source.name ||
      source.fullName ||
      source.realName ||
      source.displayName ||
      user?.name ||
      user?.fullName ||
      user?.displayName ||
      ""
  ).trim();
}

async function fetchLichessTeamMembers() {
  const text = await requestText(LICHESS_TEAM_URL);
  const trimmed = text.trim();

  if (!trimmed) {
    return [];
  }

  if (trimmed.startsWith("[")) {
    const payload = JSON.parse(trimmed);
    return payload
      .map((item) => item?.id || item?.username)
      .filter(Boolean);
  }

  return parseJsonLines(trimmed)
    .map((entry) => {
      if (typeof entry === "string") {
        return entry;
      }

      return entry?.id || entry?.username || entry?.name;
    })
    .filter(Boolean);
}

async function fetchLichessRatingHistory(username) {
  try {
    const history = await requestJson(
      `${LICHESS_USER_URL}${encodeURIComponent(username)}/rating-history`
    );

    const result = {
      blitz: { diff: null, peak: null },
      bullet: { diff: null, peak: null },
      rapid: { diff: null, peak: null }
    };

    for (const record of history || []) {
      const name = String(record?.name || "").toLowerCase();
      if (!(name in result)) {
        continue;
      }

      const points = Array.isArray(record?.points) ? record.points : [];
      const ratings = points
        .map((point) => safeInt(point?.[3], null))
        .filter((rating) => Number.isFinite(rating));

      if (!ratings.length) {
        continue;
      }

      result[name].peak = Math.max(...ratings);

      if (ratings.length >= 2) {
        result[name].diff = ratings[ratings.length - 1] - ratings[ratings.length - 2];
      } else {
        result[name].diff = 0;
      }
    }

    return result;
  } catch {
    return {
      blitz: { diff: null, peak: null },
      bullet: { diff: null, peak: null },
      rapid: { diff: null, peak: null }
    };
  }
}

async function fetchLichessUser(username) {
  try {
    const user = await requestJson(`${LICHESS_USER_URL}${encodeURIComponent(username)}`);
    const profile = user?.profile || {};
    const perfs = user?.perfs || {};
    const ratingHistory = await fetchLichessRatingHistory(username);

    return {
      username,
      name: extractRealName(profile, user),
      profile: profile.url || `https://lichess.org/@/${username}`,
      title: user?.title || null,
      country_code: profile.flag || null,
      blitz: perfs?.blitz?.rating ?? null,
      bullet: perfs?.bullet?.rating ?? null,
      rapid: perfs?.rapid?.rating ?? null,
      blitz_peak: Math.max(perfs?.blitz?.rating ?? 0, ratingHistory.blitz.peak ?? 0) || null,
      bullet_peak: Math.max(perfs?.bullet?.rating ?? 0, ratingHistory.bullet.peak ?? 0) || null,
      rapid_peak: Math.max(perfs?.rapid?.rating ?? 0, ratingHistory.rapid.peak ?? 0) || null,
      seenAt: safeTimestampMs(user?.seenAt || user?.lastSeenAt || user?.seenAtMillis),
      recent_blitz_diff: ratingHistory.blitz.diff,
      recent_bullet_diff: ratingHistory.bullet.diff,
      recent_rapid_diff: ratingHistory.rapid.diff
    };
  } catch (error) {
    console.warn(`warning: erro ao buscar Lichess user ${username}: ${error.message}`);
    return null;
  }
}

export async function generateLichessData({
  docsDir,
  writeFile = true
}) {
  const members = await fetchLichessTeamMembers();
  const results = await mapWithConcurrency(members, MAX_CONCURRENCY, fetchLichessUser);
  const players = results.filter(Boolean);

  return finalizePlayers({
    players,
    outputPath: path.join(docsDir, "players.json"),
    writeFile
  });
}
