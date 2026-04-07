import path from "node:path";
import { finalizePlayers } from "./ranking-builder.mjs";
import { requestJson } from "./http-client.mjs";
import {
  CHESSCOM_MAX_LEADERBOARD_PAGES,
  LEADERBOARD_GROUP_DELAY_MS,
  LEADERBOARD_PAGE_DELAY_MS,
  MAX_CONCURRENCY,
  SPECIAL_TITLE_OVERRIDES,
  extractCountryCodeFromUrl,
  mapWithConcurrency,
  normalizeCountryCode,
  safeInt,
  safeTimestampMs,
  sleep
} from "./shared.mjs";

const CHESSCOM_CLUB_ID = "xadrez-jovem-es";
const CHESSCOM_CLUB_MEMBERS_URL = `https://api.chess.com/pub/club/${encodeURIComponent(CHESSCOM_CLUB_ID)}/members`;
const CHESSCOM_PLAYER_URL = "https://api.chess.com/pub/player/";
const CHESSCOM_PLAYER_STATS_URL = "https://api.chess.com/pub/player/";
const CHESSCOM_LEADERBOARD_BASE_URL = "https://www.chess.com/callback/leaderboard/live";
const LEADERBOARD_SEARCH_WINDOW = 10;

function getLeaderboardUrl(rhythm) {
  const normalized = String(rhythm || "").trim().toLowerCase();
  if (!normalized || normalized === "blitz") {
    return CHESSCOM_LEADERBOARD_BASE_URL;
  }

  return `${CHESSCOM_LEADERBOARD_BASE_URL}/${normalized}`;
}

function extractChessComRating(stats, key) {
  return stats?.[key]?.last?.rating ?? null;
}

function extractChessComPeak(stats, key) {
  return stats?.[key]?.best?.rating ?? stats?.[key]?.last?.rating ?? null;
}

async function fetchChessComClubMembers() {
  const payload = await requestJson(CHESSCOM_CLUB_MEMBERS_URL);
  const usernames = [];

  for (const bucket of ["all_time", "weekly", "monthly"]) {
    for (const item of payload?.[bucket] || []) {
      const username = String(item?.username || "").trim();
      if (username) {
        usernames.push(username);
      }
    }
  }

  return [...new Set(usernames)];
}

async function fetchChessComUser(username) {
  try {
    const [profile, stats] = await Promise.all([
      requestJson(`${CHESSCOM_PLAYER_URL}${encodeURIComponent(username)}`),
      requestJson(`${CHESSCOM_PLAYER_STATS_URL}${encodeURIComponent(username)}/stats`).catch(
        () => ({})
      )
    ]);

    return {
      username,
      name: String(profile?.name || "").trim(),
      profile: profile?.url || `https://www.chess.com/member/${username}`,
      title: String(profile?.title || "").trim() || null,
      country_code: extractCountryCodeFromUrl(profile?.country),
      blitz: extractChessComRating(stats, "chess_blitz"),
      bullet: extractChessComRating(stats, "chess_bullet"),
      rapid: extractChessComRating(stats, "chess_rapid"),
      blitz_peak: extractChessComPeak(stats, "chess_blitz"),
      bullet_peak: extractChessComPeak(stats, "chess_bullet"),
      rapid_peak: extractChessComPeak(stats, "chess_rapid"),
      seenAt: safeTimestampMs(profile?.last_online)
    };
  } catch (error) {
    console.warn(`warning: erro ao buscar Chess.com user ${username}: ${error.message}`);
    return null;
  }
}

class CountryLeaderboardIndex {
  constructor(countryCode, rhythm) {
    this.countryCode = countryCode;
    this.rhythm = rhythm;
    this.pageCache = new Map();
  }

  async getPage(page) {
    if (page < 1 || page > CHESSCOM_MAX_LEADERBOARD_PAGES) {
      return {
        page,
        leaders: [],
        firstScore: 0,
        lastScore: 0
      };
    }

    if (this.pageCache.has(page)) {
      return this.pageCache.get(page);
    }

    const url = new URL(getLeaderboardUrl(this.rhythm));
    url.searchParams.set("country", this.countryCode);
    url.searchParams.set("page", String(page));
    url.searchParams.set("chessType", "chess");
    url.searchParams.set("gameType", "live");

    const payload = await requestJson(url.toString(), {
      headers: {
        Accept: "application/json, text/plain, */*",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        Referer: `https://www.chess.com/leaderboard/live/${this.rhythm}`,
        "X-Requested-With": "XMLHttpRequest"
      },
      retries: 6,
      retryDelayMs: 1_500,
      retryOnStatusCodes: [403]
    });

    const leaders = Array.isArray(payload?.leaders) ? payload.leaders : [];
    const entry = {
      page,
      leaders,
      firstScore: safeInt(leaders[0]?.score, 0),
      lastScore: safeInt(leaders[leaders.length - 1]?.score, 0)
    };

    this.pageCache.set(page, entry);
    await sleep(LEADERBOARD_PAGE_DELAY_MS);
    return entry;
  }

  async getUpperBound(minRating) {
    let page = 1;
    let current = await this.getPage(page);

    if (!current.leaders.length || current.lastScore < minRating) {
      return 1;
    }

    while (page < CHESSCOM_MAX_LEADERBOARD_PAGES) {
      const nextPage = Math.min(page * 2, CHESSCOM_MAX_LEADERBOARD_PAGES);
      if (nextPage === page) {
        return page;
      }

      const next = await this.getPage(nextPage);
      if (!next.leaders.length || next.lastScore < minRating) {
        return nextPage;
      }

      page = nextPage;
      current = next;
    }

    return current.page;
  }

  async findCandidatePage(targetRating, upperBound) {
    let low = 1;
    let high = Math.max(1, upperBound);
    let candidate = high;

    while (low <= high) {
      const mid = Math.floor((low + high) / 2);
      const page = await this.getPage(mid);

      if (!page.leaders.length) {
        high = mid - 1;
        candidate = Math.max(1, mid - 1);
        continue;
      }

      if (targetRating > page.firstScore) {
        candidate = mid;
        high = mid - 1;
        continue;
      }

      if (targetRating < page.lastScore) {
        low = mid + 1;
        candidate = low;
        continue;
      }

      return mid;
    }

    return Math.max(1, Math.min(upperBound, candidate));
  }

  async findPlayer(username, rating, upperBound) {
    const targetUsername = String(username || "").trim().toLowerCase();
    const candidatePage = await this.findCandidatePage(rating, upperBound);
    const pagesToInspect = [];

    for (let offset = 0; offset <= LEADERBOARD_SEARCH_WINDOW; offset += 1) {
      pagesToInspect.push(candidatePage - offset);
      if (offset !== 0) {
        pagesToInspect.push(candidatePage + offset);
      }
    }

    const uniquePages = [...new Set(pagesToInspect)]
      .filter((page) => page >= 1 && page <= upperBound)
      .sort((left, right) => Math.abs(left - candidatePage) - Math.abs(right - candidatePage));

    for (const pageNumber of uniquePages) {
      const page = await this.getPage(pageNumber);
      const match = page.leaders.find((leader) => {
        return String(leader?.user?.username || "").trim().toLowerCase() === targetUsername;
      });

      if (match) {
        return match;
      }
    }

    return null;
  }
}

async function enrichChessComCountryRanks(players) {
  const groups = new Map();

  for (const player of players) {
    const username = String(player?.username || "").trim().toLowerCase();
    const countryCode = normalizeCountryCode(player?.country_code);

    if (!username || !countryCode) {
      continue;
    }

    for (const rhythm of ["blitz", "bullet", "rapid"]) {
      const rating = safeInt(player?.[rhythm], 0);
      if (rating <= 0) {
        player[`${rhythm}_country_rank`] = null;
        continue;
      }

      const key = `${countryCode}::${rhythm}`;
      if (!groups.has(key)) {
        groups.set(key, {
          countryCode,
          rhythm,
          targets: new Map()
        });
      }

      groups.get(key).targets.set(username, {
        player,
        rating
      });
    }
  }

  for (const group of groups.values()) {
    const entries = [...group.targets.entries()].sort((left, right) => right[1].rating - left[1].rating);
    const minimumRating = entries[entries.length - 1]?.[1]?.rating || 0;
    const leaderboard = new CountryLeaderboardIndex(group.countryCode, group.rhythm);

    let upperBound = 1;
    try {
      upperBound = await leaderboard.getUpperBound(minimumRating);
    } catch (error) {
      console.warn(
        `warning: erro ao preparar leaderboard ${group.countryCode}/${group.rhythm}: ${error.message}`
      );
      continue;
    }

    for (const [username, target] of entries) {
      try {
        const match = await leaderboard.findPlayer(username, target.rating, upperBound);
        if (!match) {
          continue;
        }

        const user = match.user || {};
        target.player[`${group.rhythm}_country_rank`] = safeInt(match.rank, null);

        if (!target.player.title) {
          target.player.title =
            SPECIAL_TITLE_OVERRIDES[username] || String(user?.chess_title || "").trim() || null;
        }

        if (!target.player.country_name) {
          target.player.country_name = String(user?.country_name || "").trim() || null;
        }
      } catch (error) {
        console.warn(
          `warning: erro localizando rank ${group.countryCode}/${group.rhythm}/${username}: ${error.message}`
        );
      }
    }

    await sleep(LEADERBOARD_GROUP_DELAY_MS);
  }
}

export async function generateChessComData({
  docsDir,
  writeFile = true
}) {
  const members = await fetchChessComClubMembers();
  const results = await mapWithConcurrency(members, MAX_CONCURRENCY, fetchChessComUser);
  const players = results.filter(Boolean);

  return finalizePlayers({
    players,
    outputPath: path.join(docsDir, "players_chesscom.json"),
    writeFile,
    enrichPlayers: enrichChessComCountryRanks
  });
}
