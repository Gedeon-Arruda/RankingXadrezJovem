import { DEFAULTS, RHYTHMS } from "./config.js";

const TITLE_OVERRIDES = Object.freeze({
  gedevonarrudev: "DEV",
  gedevon_arrudev: "DEV"
});

const HONOR_BADGE_OVERRIDES = Object.freeze({
  // Adicione novos campeoes estaduais manualmente aqui.
  jwmrocha: Object.freeze({
    label: "CE",
    tooltip: "Campeão estadual"
  }),
  normanfrieman: Object.freeze({
    label: "CE",
    tooltip: "Campeão estadual"
  })
});

const COUNTRY_DISPLAY_NAMES =
  typeof Intl !== "undefined" && typeof Intl.DisplayNames === "function"
    ? new Intl.DisplayNames(["pt-BR", "en"], { type: "region" })
    : null;

export function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, (char) => {
    return {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    }[char];
  });
}

export function safeNumber(value, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

export function toTimestamp(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }

  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return null;
  }

  return parsed < 1_000_000_000_000 ? parsed * 1000 : parsed;
}

export function formatNumber(value, fallback = "--") {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return fallback;
  }

  return numeric.toLocaleString("pt-BR");
}

export function formatDateTime(value) {
  const timestamp = toTimestamp(value);
  if (!timestamp) {
    return "--";
  }

  return new Date(timestamp).toLocaleString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  });
}

export function formatRelativeFromNow(value) {
  const timestamp = toTimestamp(value);
  if (!timestamp) {
    return "";
  }

  const diffMs = Date.now() - timestamp;
  const diffMinutes = Math.max(0, Math.floor(diffMs / 60_000));

  if (diffMinutes < 1) {
    return "agora";
  }

  if (diffMinutes < 60) {
    return `há ${diffMinutes} min`;
  }

  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) {
    return `há ${diffHours} h`;
  }

  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 30) {
    return `há ${diffDays} dia${diffDays > 1 ? "s" : ""}`;
  }

  const diffMonths = Math.floor(diffDays / 30);
  return `há ${diffMonths} mês${diffMonths > 1 ? "es" : ""}`;
}

export function formatSeenCompact(value) {
  const timestamp = toTimestamp(value);
  if (!timestamp) {
    return "sem info";
  }

  return formatRelativeFromNow(timestamp);
}

export function formatSeenTitle(value) {
  const timestamp = toTimestamp(value);
  if (!timestamp) {
    return "Último login indisponível";
  }

  return `Último login: ${formatDateTime(timestamp)}`;
}

export function formatDiff(value) {
  const numeric = safeNumber(value, 0);
  if (numeric > 0) {
    return `+${numeric}`;
  }

  if (numeric < 0) {
    return String(numeric);
  }

  return "0";
}

export function getDiffClass(value) {
  const numeric = safeNumber(value, 0);
  if (numeric > 0) return "diff diff-pos";
  if (numeric < 0) return "diff diff-neg";
  return "diff diff-zero";
}

export function normalizeArrow(value) {
  const text = String(value || "").trim();

  if (!text) {
    return "";
  }

  if (text === "\u25B2" || text.includes("â–²") || text.includes("Ã¢â€“Â²")) {
    return "\u25B2";
  }

  if (text === "\u25BC" || text.includes("â–¼") || text.includes("Ã¢â€“Â¼")) {
    return "\u25BC";
  }

  if (text === "\u2192" || text.includes("â†’") || text.includes("Ã¢â€ â€™")) {
    return "\u2192";
  }

  return text;
}

export function getArrowClass(arrow) {
  if (arrow === "\u25B2") return "pos-up";
  if (arrow === "\u25BC") return "pos-down";
  return "pos-same";
}

export function getPlayerName(player) {
  return String(
    player?.name ||
      player?.realname ||
      player?.fullName ||
      player?.fullname ||
      "Sem nome registrado"
  ).trim();
}

export function getUsernameKey(username) {
  return String(username || "").trim().toLowerCase();
}

export function getPlayerTitle(player) {
  const usernameKey = getUsernameKey(player?.username);
  const explicitTitle = String(player?.title || "").trim().toUpperCase();
  return TITLE_OVERRIDES[usernameKey] || explicitTitle || null;
}

export function getPlayerHonorBadge(player) {
  const usernameKey = getUsernameKey(player?.username);
  return HONOR_BADGE_OVERRIDES[usernameKey] || null;
}

export function getCountryCode(player) {
  const code = String(player?.country_code || "").trim().toUpperCase();
  return code || null;
}

export function getCountryName(player) {
  if (player?.country_name) {
    return String(player.country_name).trim();
  }

  const code = getCountryCode(player);
  if (!code || !COUNTRY_DISPLAY_NAMES) {
    return "";
  }

  try {
    return COUNTRY_DISPLAY_NAMES.of(code) || "";
  } catch {
    return "";
  }
}

export function getCountryRankValue(player, rhythm) {
  const value = Number(player?.[`${rhythm}_country_rank`]);
  return Number.isFinite(value) && value > 0 ? value : null;
}

export function getCountryRankText(player, rhythm, sourceId) {
  if (sourceId !== "chesscom") {
    return "";
  }

  const code = getCountryCode(player);
  if (!code) {
    return "";
  }

  const rank = getCountryRankValue(player, rhythm);
  if (rank) {
    return `${code} #${formatNumber(rank)}`;
  }

  return `${code} não listado`;
}

export function getCountryRankMuted(player, rhythm, sourceId) {
  return Boolean(sourceId === "chesscom" && getCountryCode(player) && !getCountryRankValue(player, rhythm));
}

export function getProfileUrl(player, sourceConfig) {
  const explicit = String(player?.profile || player?.url || "").trim();
  if (explicit) {
    return explicit;
  }

  const username = String(player?.username || "").trim();
  return `${sourceConfig.profileBase}${encodeURIComponent(username)}`;
}

export function getPeakRating(player, rhythm) {
  const peakValue = Number(player?.[`${rhythm}_peak`]);
  if (Number.isFinite(peakValue) && peakValue > 0) {
    return peakValue;
  }

  const currentValue = Number(player?.[rhythm]);
  if (Number.isFinite(currentValue) && currentValue > 0) {
    return currentValue;
  }

  return null;
}

export function extractPlayers(payload) {
  if (Array.isArray(payload?.players)) {
    return payload.players;
  }

  if (Array.isArray(payload)) {
    return payload;
  }

  return [];
}

export function dedupePlayers(players) {
  const byUsername = new Map();

  const getScore = (player) =>
    RHYTHMS.reduce((sum, rhythm) => sum + safeNumber(player?.[rhythm], 0), 0);

  for (const player of players) {
    const usernameKey = getUsernameKey(player?.username);
    if (!usernameKey) {
      continue;
    }

    const previous = byUsername.get(usernameKey);
    if (!previous) {
      byUsername.set(usernameKey, player);
      continue;
    }

    const currentScore = getScore(player);
    const previousScore = getScore(previous);
    const currentSeen = toTimestamp(player?.seenAt) || 0;
    const previousSeen = toTimestamp(previous?.seenAt) || 0;

    if (currentScore > previousScore || currentSeen > previousSeen) {
      byUsername.set(usernameKey, player);
    }
  }

  return [...byUsername.values()];
}

export function filterPlayers(players, query) {
  const normalizedQuery = String(query || "").trim().toLowerCase();
  if (!normalizedQuery) {
    return [...players];
  }

  return players.filter((player) => {
    const username = String(player?.username || "").toLowerCase();
    const realName = getPlayerName(player).toLowerCase();
    return username.includes(normalizedQuery) || realName.includes(normalizedQuery);
  });
}

export function sortPlayers(players, sortKey, order) {
  const direction = order === "asc" ? 1 : -1;
  const sorted = [...players];

  sorted.sort((left, right) => {
    if (sortKey === "username") {
      const leftValue = String(left?.username || "").toLowerCase();
      const rightValue = String(right?.username || "").toLowerCase();
      return leftValue.localeCompare(rightValue) * direction;
    }

    if (sortKey === "position") {
      const leftValue = safeNumber(left?.position, Number.MAX_SAFE_INTEGER);
      const rightValue = safeNumber(right?.position, Number.MAX_SAFE_INTEGER);
      return (leftValue - rightValue) * direction;
    }

    const leftValue = safeNumber(left?.[sortKey], 0);
    const rightValue = safeNumber(right?.[sortKey], 0);

    if (leftValue !== rightValue) {
      return (leftValue - rightValue) * direction;
    }

    const leftPosition = safeNumber(left?.position, Number.MAX_SAFE_INTEGER);
    const rightPosition = safeNumber(right?.position, Number.MAX_SAFE_INTEGER);
    return (leftPosition - rightPosition) * direction;
  });

  return sorted;
}

export function formatInfoLine(sourceLabel, count, generatedAt) {
  const parts = [`Fonte: ${sourceLabel}`, `${formatNumber(count, "0")} jogador(es)`];

  if (generatedAt) {
    const relative = formatRelativeFromNow(generatedAt);
    const updatedLabel = relative
      ? `atualizado: ${formatDateTime(generatedAt)} (${relative})`
      : `atualizado: ${formatDateTime(generatedAt)}`;
    parts.push(updatedLabel);
  }

  return parts.join(" | ");
}

export function isValidSelectValue(selectElement, value) {
  return Array.from(selectElement?.options || []).some((option) => option.value === value);
}

export function hasActiveFilters({ query, sort, order, perPage, source }) {
  return Boolean(
    String(query || "").trim() ||
      sort !== DEFAULTS.sort ||
      order !== DEFAULTS.order ||
      perPage !== DEFAULTS.perPage ||
      source !== DEFAULTS.source
  );
}
